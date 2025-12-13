"""API endpoints for categorization analytics and auditing."""

from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import Transaction, TransactionSummary, PLRowMapping, TaxReturn
from app.schemas.transactions import TransactionResponse

router = APIRouter(prefix="/api/categorization", tags=["categorization"])

# Add web router for UI pages
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

web_router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/{tax_return_id}/analytics")
async def get_categorization_analytics(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> Dict:
    """Get categorization analytics for a tax return."""

    # Verify tax return exists
    tax_return_result = await db.execute(
        select(TaxReturn).where(TaxReturn.id == tax_return_id)
    )
    tax_return = tax_return_result.scalar_one_or_none()
    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    # 1. Categorization source breakdown
    source_result = await db.execute(
        select(
            Transaction.categorization_source,
            func.count(Transaction.id).label('count'),
            func.avg(Transaction.confidence).label('avg_confidence')
        )
        .where(Transaction.tax_return_id == tax_return_id)
        .group_by(Transaction.categorization_source)
    )

    source_breakdown = {}
    for row in source_result.all():
        source = row[0] or 'unknown'
        source_breakdown[source] = {
            'count': row[1],
            'avg_confidence': float(row[2]) if row[2] else 0.0
        }

    # 2. Category breakdown with sources
    category_result = await db.execute(
        select(
            Transaction.category_code,
            Transaction.categorization_source,
            func.count(Transaction.id).label('count'),
            func.sum(Transaction.amount).label('total_amount')
        )
        .where(Transaction.tax_return_id == tax_return_id)
        .group_by(Transaction.category_code, Transaction.categorization_source)
    )

    category_breakdown = {}
    for row in category_result.all():
        category = row[0] or 'uncategorized'
        source = row[1] or 'unknown'

        if category not in category_breakdown:
            category_breakdown[category] = {
                'total_count': 0,
                'total_amount': 0,
                'sources': {}
            }

        category_breakdown[category]['total_count'] += row[2]
        category_breakdown[category]['total_amount'] += float(row[3])
        category_breakdown[category]['sources'][source] = {
            'count': row[2],
            'amount': float(row[3])
        }

    # 3. Confidence distribution
    from sqlalchemy import case

    confidence_result = await db.execute(
        select(
            case(
                (Transaction.confidence >= 0.95, '95-100%'),
                (Transaction.confidence >= 0.85, '85-95%'),
                (Transaction.confidence >= 0.70, '70-85%'),
                (Transaction.confidence >= 0.50, '50-70%')
            , else_='<50%').label('confidence_range'),
            func.count(Transaction.id).label('count')
        )
        .where(
            and_(
                Transaction.tax_return_id == tax_return_id,
                Transaction.confidence.isnot(None)
            )
        )
        .group_by('confidence_range')
    )

    confidence_distribution = {
        row[0]: row[1] for row in confidence_result.all()
    }

    # 4. Review status
    review_result = await db.execute(
        select(
            Transaction.needs_review,
            Transaction.categorization_source,
            func.count(Transaction.id).label('count')
        )
        .where(Transaction.tax_return_id == tax_return_id)
        .group_by(Transaction.needs_review, Transaction.categorization_source)
    )

    review_breakdown = {'needs_review': {}, 'ok': {}}
    for row in review_result.all():
        status = 'needs_review' if row[0] else 'ok'
        source = row[1] or 'unknown'
        review_breakdown[status][source] = row[2]

    # 5. Get transactions needing review with reasons
    review_transactions_result = await db.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.tax_return_id == tax_return_id,
                Transaction.needs_review == True
            )
        )
        .limit(10)
    )
    review_transactions = review_transactions_result.scalars().all()

    review_samples = []
    for trans in review_transactions:
        review_samples.append({
            'id': str(trans.id),
            'date': trans.transaction_date.isoformat(),
            'description': trans.description[:100],
            'amount': float(trans.amount),
            'category': trans.category_code,
            'confidence': trans.confidence,
            'source': trans.categorization_source,
            'reason': trans.review_reason
        })

    # 6. Pattern matching effectiveness (for YAML and learned patterns)
    pattern_stats = {
        'yaml_patterns': 0,
        'learned_patterns': 0,
        'claude_categorized': 0,
        'extraction_categorized': 0
    }

    # Count by source
    for source, data in source_breakdown.items():
        if source == 'yaml_pattern' or source == 'yaml_payee':
            pattern_stats['yaml_patterns'] += data['count']
        elif source == 'learned_exact' or source == 'learned_fuzzy':
            pattern_stats['learned_patterns'] += data['count']
        elif source == 'claude':
            pattern_stats['claude_categorized'] += data['count']
        elif source == 'extraction':
            pattern_stats['extraction_categorized'] += data['count']

    return {
        'tax_return_id': str(tax_return_id),
        'property_address': tax_return.property_address,
        'analytics': {
            'source_breakdown': source_breakdown,
            'category_breakdown': category_breakdown,
            'confidence_distribution': confidence_distribution,
            'review_breakdown': review_breakdown,
            'pattern_effectiveness': pattern_stats,
            'review_samples': review_samples
        }
    }


@router.get("/{tax_return_id}/audit-report")
async def get_categorization_audit_report(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> Dict:
    """Generate a detailed categorization audit report."""

    # Get analytics first
    analytics = await get_categorization_analytics(tax_return_id, db)

    # Add detailed category analysis
    category_details = []

    # Get all categories with P&L mappings
    pl_result = await db.execute(
        select(PLRowMapping)
        .order_by(PLRowMapping.pl_row)
    )
    pl_mappings = {m.category_code: m for m in pl_result.scalars().all()}

    for category_code, data in analytics['analytics']['category_breakdown'].items():
        pl_mapping = pl_mappings.get(category_code)

        category_detail = {
            'category_code': category_code,
            'display_name': pl_mapping.display_name if pl_mapping else category_code,
            'pl_row': pl_mapping.pl_row if pl_mapping else None,
            'transaction_type': pl_mapping.transaction_type if pl_mapping else 'unknown',
            'total_count': data['total_count'],
            'total_amount': data['total_amount'],
            'categorization_methods': []
        }

        # Break down by source
        for source, source_data in data['sources'].items():
            method = {
                'source': source,
                'count': source_data['count'],
                'amount': source_data['amount'],
                'percentage': (source_data['count'] / data['total_count'] * 100) if data['total_count'] > 0 else 0
            }
            category_detail['categorization_methods'].append(method)

        category_details.append(category_detail)

    # Sort by P&L row for easy review
    category_details.sort(key=lambda x: (x['pl_row'] or 999, x['category_code']))

    # Generate summary insights
    insights = []

    # Check for high manual/Claude categorization
    claude_pct = (analytics['analytics']['pattern_effectiveness']['claude_categorized'] /
                  sum(analytics['analytics']['source_breakdown'][s]['count']
                      for s in analytics['analytics']['source_breakdown']) * 100
                  if analytics['analytics']['source_breakdown'] else 0)

    if claude_pct > 30:
        insights.append({
            'type': 'warning',
            'message': f'{claude_pct:.1f}% of transactions required AI categorization. Consider adding more patterns.'
        })

    # Check confidence levels
    low_confidence = analytics['analytics']['confidence_distribution'].get('<50%', 0)
    if low_confidence > 0:
        insights.append({
            'type': 'warning',
            'message': f'{low_confidence} transactions have very low confidence (<50%)'
        })

    # Check review needed
    total_review = sum(analytics['analytics']['review_breakdown']['needs_review'].values())
    if total_review > 0:
        insights.append({
            'type': 'info',
            'message': f'{total_review} transactions need manual review'
        })

    return {
        'tax_return_id': str(tax_return_id),
        'property_address': analytics['property_address'],
        'generated_at': datetime.utcnow().isoformat(),
        'summary': {
            'total_transactions': sum(s['count'] for s in analytics['analytics']['source_breakdown'].values()),
            'categorization_sources': analytics['analytics']['source_breakdown'],
            'pattern_effectiveness': analytics['analytics']['pattern_effectiveness'],
            'confidence_distribution': analytics['analytics']['confidence_distribution'],
            'review_required': total_review
        },
        'category_details': category_details,
        'insights': insights,
        'review_samples': analytics['analytics']['review_samples']
    }


@router.get("/{tax_return_id}/problem-transactions")
async def get_problem_transactions(
    tax_return_id: UUID,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
) -> List[Dict]:
    """Get transactions that may have categorization issues."""

    # Find transactions with potential issues
    result = await db.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.tax_return_id == tax_return_id,
                or_(
                    Transaction.needs_review == True,
                    Transaction.confidence < 0.5,
                    Transaction.category_code == 'unknown',
                    Transaction.category_code == None
                )
            )
        )
        .order_by(Transaction.confidence.asc(), Transaction.amount.desc())
        .limit(limit)
    )

    transactions = result.scalars().all()

    problem_transactions = []
    for trans in transactions:
        problem = {
            'id': str(trans.id),
            'date': trans.transaction_date.isoformat(),
            'description': trans.description,
            'amount': float(trans.amount),
            'category': trans.category_code or 'uncategorized',
            'confidence': trans.confidence,
            'source': trans.categorization_source,
            'issues': []
        }

        # Identify specific issues
        if trans.needs_review:
            problem['issues'].append(f'Needs review: {trans.review_reason or "No reason specified"}')
        if trans.confidence and trans.confidence < 0.5:
            problem['issues'].append(f'Low confidence: {trans.confidence:.2f}')
        if not trans.category_code or trans.category_code == 'unknown':
            problem['issues'].append('Uncategorized or unknown category')

        problem_transactions.append(problem)

    return problem_transactions


@router.get("/{tax_return_id}/category-transactions/{category_code}")
async def get_category_transactions(
    tax_return_id: UUID,
    category_code: str,
    db: AsyncSession = Depends(get_db)
) -> List[Dict]:
    """Get all transactions for a specific category."""

    # Get transactions for this category
    result = await db.execute(
        select(Transaction)
        .where(
            and_(
                Transaction.tax_return_id == tax_return_id,
                Transaction.category_code == category_code
            )
        )
        .order_by(Transaction.transaction_date.desc())
    )

    transactions = result.scalars().all()

    transaction_details = []
    for trans in transactions:
        detail = {
            'id': str(trans.id),
            'date': trans.transaction_date.isoformat(),
            'description': trans.description,
            'other_party': trans.other_party,
            'amount': float(trans.amount),
            'category_code': trans.category_code,
            'confidence': trans.confidence,
            'categorization_source': trans.categorization_source,
            'needs_review': trans.needs_review,
            'review_reason': trans.review_reason,
            'manually_reviewed': trans.manually_reviewed,
            'is_deductible': trans.is_deductible,
            'deductible_percentage': trans.deductible_percentage,
            'categorization_trace': trans.categorization_trace
        }
        transaction_details.append(detail)

    return transaction_details


@web_router.get("/categorization-audit/{tax_return_id}", response_class=HTMLResponse)
async def categorization_audit_page(
    request: Request,
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Render the categorization audit report page."""
    # Get tax return details
    tax_return_result = await db.execute(
        select(TaxReturn).where(TaxReturn.id == tax_return_id)
    )
    tax_return = tax_return_result.scalar_one_or_none()

    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    return templates.TemplateResponse(
        "categorization_audit.html",
        {
            "request": request,
            "tax_return_id": str(tax_return_id),
            "property_address": tax_return.property_address,
            "tax_year": tax_return.tax_year
        }
    )