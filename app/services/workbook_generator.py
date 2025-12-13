"""Workbook generator service for creating Lighthouse Financial property tax workbooks.

Updated to match exact Lighthouse Financial template with 2 sheets:
- Profit and Loss (with P&L left, workings right)
- IRD (compliance checklist)
"""
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.db_models import (
    Document,
    PLRowMapping,
    TaxReturn,
    Transaction,
    TransactionSummary,
)
from app.services.tax_rules_service import get_tax_rules_service

logger = logging.getLogger(__name__)


# =============================================================================
# STYLES
# =============================================================================

def create_styles(wb: Workbook):
    """Create named styles for the workbook."""

    # Header style - bold
    header_style = NamedStyle(name="header")
    header_style.font = Font(bold=True, size=11)
    header_style.alignment = Alignment(horizontal="left", vertical="center")
    wb.add_named_style(header_style)

    # Currency style
    currency_style = NamedStyle(name="currency")
    currency_style.number_format = '"$"#,##0.00'
    currency_style.alignment = Alignment(horizontal="right")
    wb.add_named_style(currency_style)

    # Currency negative style (red)
    currency_neg_style = NamedStyle(name="currency_neg")
    currency_neg_style.number_format = '"$"#,##0.00;[Red]-"$"#,##0.00'
    currency_neg_style.alignment = Alignment(horizontal="right")
    wb.add_named_style(currency_neg_style)

    # Percentage style
    percent_style = NamedStyle(name="percent")
    percent_style.number_format = '0%'
    percent_style.alignment = Alignment(horizontal="right")
    wb.add_named_style(percent_style)

    # Date style
    date_style = NamedStyle(name="date_style")
    date_style.number_format = 'DD/MM/YYYY'
    date_style.alignment = Alignment(horizontal="left")
    wb.add_named_style(date_style)

    # Section header
    section_style = NamedStyle(name="section")
    section_style.font = Font(bold=True, size=11)
    section_style.fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")
    wb.add_named_style(section_style)

    # Total row
    total_style = NamedStyle(name="total")
    total_style.font = Font(bold=True)
    total_style.border = Border(top=Side(style="thin"), bottom=Side(style="double"))
    total_style.number_format = '"$"#,##0.00'
    wb.add_named_style(total_style)

    # Small text
    small_style = NamedStyle(name="small")
    small_style.font = Font(size=9)
    wb.add_named_style(small_style)


class WorkbookGenerator:
    """Generate Lighthouse Financial template workbooks from processed transactions."""

    def __init__(self):
        """Initialize workbook generator."""
        self.tax_rules_service = get_tax_rules_service()
        self.output_dir = settings.UPLOAD_DIR / "workbooks"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate_workbook(
        self,
        db: AsyncSession,
        tax_return_id: UUID
    ) -> Path:
        """
        Generate complete Lighthouse Financial workbook for a tax return.

        Args:
            db: Database session
            tax_return_id: Tax return to generate workbook for

        Returns:
            Path to generated workbook file
        """
        # Load all data
        tax_return = await self._load_tax_return(db, tax_return_id)
        transactions = await self._load_transactions(db, tax_return_id)
        summaries = await self._load_summaries(db, tax_return_id)
        pl_mappings = await self._load_pl_mappings(db)

        # Get tax rules
        interest_deductibility = await self.tax_rules_service.get_interest_deductibility(
            db, tax_return.tax_year, tax_return.property_type.value
        )

        # Create workbook
        wb = Workbook()
        create_styles(wb)

        # Remove default sheet
        default_sheet = wb.active

        # Create sheets
        pl_sheet = wb.create_sheet("Profit and Loss", 0)
        ird_sheet = wb.create_sheet("IRD", 1)

        logger.info(f"Created new workbook with sheets: {wb.sheetnames}")

        # Build context
        context = {
            "tax_return": tax_return,
            "transactions": transactions,
            "summaries": summaries,
            "pl_mappings": pl_mappings,
            "interest_deductibility": interest_deductibility,
        }

        # Remove default sheet
        if default_sheet and default_sheet.title == "Sheet":
            wb.remove(default_sheet)

        # Build sheets
        self._build_profit_loss_sheet(pl_sheet, context)
        self._build_ird_sheet(ird_sheet, context)

        # Set P&L as active
        wb.active = pl_sheet

        # Generate filename matching template
        client_name = self._sanitize_filename(tax_return.client.name)
        year = tax_return.tax_year[-2:]  # FY24 -> 24
        filename = f"PTR01_-_Rental_Property_Workbook_-_{client_name}_-_{year}.xlsx"
        filepath = self.output_dir / filename

        # Save
        wb.save(filepath)
        logger.info(f"Generated workbook: {filepath}")

        return filepath

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize string for use in filename."""
        return "".join(c if c.isalnum() or c in "_- " else "_" for c in name).replace(" ", "_")

    def _build_profit_loss_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the Profit and Loss sheet matching Lighthouse template."""
        tax_return = context["tax_return"]
        transactions = context["transactions"]
        summaries = context["summaries"]
        pl_mappings = context["pl_mappings"]
        interest_deductibility = context["interest_deductibility"]

        # Create lookups
        summary_by_category = {s.category_code: s for s in summaries}
        mapping_by_code = {m.category_code: m for m in pl_mappings}

        # Set column widths
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 2
        ws.column_dimensions["C"].width = 15
        ws.column_dimensions["D"].width = 8
        ws.column_dimensions["E"].width = 2
        ws.column_dimensions["F"].width = 2
        ws.column_dimensions["G"].width = 2
        ws.column_dimensions["H"].width = 2
        ws.column_dimensions["I"].width = 2
        ws.column_dimensions["J"].width = 40
        ws.column_dimensions["K"].width = 15
        ws.column_dimensions["L"].width = 15
        ws.column_dimensions["M"].width = 15
        ws.column_dimensions["N"].width = 15
        ws.column_dimensions["O"].width = 15
        ws.column_dimensions["P"].width = 15
        ws.column_dimensions["Q"].width = 15

        # ===== LEFT SIDE - P&L SUMMARY =====

        # Client details (Rows 1-5)
        ws["A1"] = "Client Name"
        ws["B1"] = client_name = tax_return.client.name
        ws["D1"] = "Business Commencement Date"
        ws["E1"] = ""  # Would need this field

        ws["A2"] = "Resident for Tax Purposes"
        ws["B2"] = "Y"

        ws["C4"] = "Property Ownership"
        ws["C5"] = tax_return.property_address
        ws["E5"] = f"Property Type: {'New Build' if tax_return.property_type.value == 'new_build' else 'Existing'}"
        ws["F5"] = f"Year: {tax_return.year_of_ownership}"

        # Income section (Rows 6-9)
        row = 6
        ws[f"A{row}"] = "Rental Income"
        income_summary = summary_by_category.get("rental_income")
        ws[f"C{row}"] = float(abs(income_summary.gross_amount)) if income_summary else 0
        ws[f"C{row}"].style = "currency"
        ws[f"D{row}"] = "BS/PM"

        row = 7
        ws[f"A{row}"] = "Water Rates Recovered"
        water_rec_summary = summary_by_category.get("water_rates_recovered")
        ws[f"C{row}"] = float(abs(water_rec_summary.gross_amount)) if water_rec_summary else 0
        ws[f"C{row}"].style = "currency"
        ws[f"D{row}"] = "BS"

        row = 8
        ws[f"A{row}"] = "Bank Contribution"
        bank_contrib_summary = summary_by_category.get("bank_contribution")
        ws[f"C{row}"] = float(abs(bank_contrib_summary.gross_amount)) if bank_contrib_summary else 0
        ws[f"C{row}"].style = "currency"
        ws[f"D{row}"] = "BS"

        row = 9
        ws[f"A{row}"] = "Total Income"
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"C{row}"] = f"=SUM(C6:C8)"
        ws[f"C{row}"].style = "total"

        # Expenses section (Rows 11-43)
        row = 11
        ws[f"A{row}"] = "Expenses"
        ws[f"A{row}"].font = Font(bold=True)

        # Map categories to rows with Lighthouse template row numbers
        expense_rows = [
            (12, "advertising", "Advertising"),
            (13, "agent_fees", "Agent Fees"),
            (14, "assets_under_500", "Assets Under $500"),
            (15, "bank_fees", "Bank Fees"),
            (16, "cleaning", "Cleaning"),
            (17, "consulting_accounting", "Consulting & Accounting"),
            (18, "depreciation", "Depreciation"),
            (19, "due_diligence", "Due Diligence"),
            (20, "entertainment", "Entertainment"),
            (21, "entertainment_non_deductible", "Entertainment - Non deductible"),
            (22, "freight_courier", "Freight & Courier"),
            (23, "general_expenses", "General Expenses"),
            (24, "home_office", "Home Office Expense"),
            (25, "insurance", "Insurance"),
            (26, "interest", "Interest Expense"),
            (27, "legal_fees", "Legal Expenses"),
            (28, "electricity", "Light, Power, Heating"),
            (29, "loss_on_disposal", "Loss on Disposal of Fixed Asset"),
            (30, "vehicle_expenses", "Motor Vehicle Expenses"),
            (31, "office_expenses", "Office Expenses"),
            (32, "overdraft_interest", "Overdraft Interest"),
            (33, "printing_stationery", "Printing & Stationery"),
            (34, "rates", "Rates"),
            (35, "repairs_maintenance", "Repairs & Maintenance"),
            (36, "shareholder_salary", "Shareholder Salary"),
            (37, "subscriptions", "Subscriptions"),
            (38, "telephone_internet", "Telephone & Internet"),
            (39, "travel_national", "Travel - National"),
            (40, "travel_international", "Travel - International"),
            (41, "water_rates", "Water Rates"),
        ]

        for row_num, category_code, display_name in expense_rows:
            ws[f"A{row_num}"] = display_name

            if category_code == "consulting_accounting":
                # Fixed accounting fee
                ws[f"C{row_num}"] = 862.50
                ws[f"D{row_num}"] = "AF"
            elif category_code == "interest":
                # Link to interest workings
                ws[f"C{row_num}"] = f"=K47"
                ws[f"D{row_num}"] = "IW"
            else:
                # Get from summaries
                summary = summary_by_category.get(category_code)
                if summary:
                    ws[f"C{row_num}"] = float(abs(summary.deductible_amount or summary.gross_amount or 0))
                    mapping = mapping_by_code.get(category_code)
                    ws[f"D{row_num}"] = mapping.default_source if mapping else "BS"
                else:
                    ws[f"C{row_num}"] = 0

            ws[f"C{row_num}"].style = "currency"

        # Total expenses (Row 43)
        row = 43
        ws[f"A{row}"] = "Total Expenses"
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"C{row}"] = f"=SUM(C12:C41)"
        ws[f"C{row}"].style = "total"

        # Net income (Row 44)
        row = 44
        ws[f"A{row}"] = "Net Income"
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"C{row}"] = f"=C9-C43"
        ws[f"C{row}"].style = "total"

        # ===== RIGHT SIDE - INTEREST & BANK STATEMENT WORKINGS =====

        # Title (Row 32)
        ws["J32"] = "Interest Deductibility and Bank Statement Workings"
        ws["J32"].font = Font(bold=True)

        # Headers (Row 33)
        ws["K33"] = "Loan Account 1"
        ws["L33"] = "Loan Account 2"
        ws["M33"] = "Loan Account 3"
        ws["N33"] = "Rates"
        ws["O33"] = "Insurance"
        ws["P33"] = "Bank Fees"

        # Group transactions by month and category
        monthly_data = self._group_transactions_by_month(transactions)

        # Month rows (34-45)
        months = [
            ("Apr-24", 34), ("May-24", 35), ("Jun-24", 36), ("Jul-24", 37),
            ("Aug-24", 38), ("Sep-24", 39), ("Oct-24", 40), ("Nov-24", 41),
            ("Dec-24", 42), ("Jan-25", 43), ("Feb-25", 44), ("Mar-25", 45)
        ]

        # Adjust months based on tax year
        if "24" in tax_return.tax_year:
            months = [
                ("Apr-23", 34), ("May-23", 35), ("Jun-23", 36), ("Jul-23", 37),
                ("Aug-23", 38), ("Sep-23", 39), ("Oct-23", 40), ("Nov-23", 41),
                ("Dec-23", 42), ("Jan-24", 43), ("Feb-24", 44), ("Mar-24", 45)
            ]
        elif "25" in tax_return.tax_year:
            months = [
                ("Apr-24", 34), ("May-24", 35), ("Jun-24", 36), ("Jul-24", 37),
                ("Aug-24", 38), ("Sep-24", 39), ("Oct-24", 40), ("Nov-24", 41),
                ("Dec-24", 42), ("Jan-25", 43), ("Feb-25", 44), ("Mar-25", 45)
            ]
        elif "26" in tax_return.tax_year:
            months = [
                ("Apr-25", 34), ("May-25", 35), ("Jun-25", 36), ("Jul-25", 37),
                ("Aug-25", 38), ("Sep-25", 39), ("Oct-25", 40), ("Nov-25", 41),
                ("Dec-25", 42), ("Jan-26", 43), ("Feb-26", 44), ("Mar-26", 45)
            ]

        for month_str, row_num in months:
            ws[f"I{row_num}"] = month_str
            ws[f"J{row_num}"] = "BS"

            # Interest (loan columns K, L, M)
            if month_str in monthly_data and "interest" in monthly_data[month_str]:
                ws[f"K{row_num}"] = float(monthly_data[month_str]["interest"])
                ws[f"K{row_num}"].style = "currency"

            # Rates (column N)
            if month_str in monthly_data and "rates" in monthly_data[month_str]:
                ws[f"N{row_num}"] = float(monthly_data[month_str]["rates"])
                ws[f"N{row_num}"].style = "currency"

            # Insurance (column O)
            if month_str in monthly_data and "insurance" in monthly_data[month_str]:
                ws[f"O{row_num}"] = float(monthly_data[month_str]["insurance"])
                ws[f"O{row_num}"].style = "currency"

            # Bank fees (column P)
            if month_str in monthly_data and "bank_fees" in monthly_data[month_str]:
                ws[f"P{row_num}"] = float(monthly_data[month_str]["bank_fees"])
                ws[f"P{row_num}"].style = "currency"

        # Totals (Row 46)
        row = 46
        ws[f"K{row}"] = f"=SUM(K34:K45)"
        ws[f"L{row}"] = f"=SUM(L34:L45)"
        ws[f"M{row}"] = f"=SUM(M34:M45)"
        ws[f"N{row}"] = f"=SUM(N34:N45)"
        ws[f"O{row}"] = f"=SUM(O34:O45)"
        ws[f"P{row}"] = f"=SUM(P34:P45)"
        for col in ["K", "L", "M", "N", "O", "P"]:
            ws[f"{col}{row}"].style = "total"

        # Deductible (Row 47)
        row = 47
        ws[f"J{row}"] = "Deductible"
        deductible_rate = interest_deductibility / 100
        ws[f"K{row}"] = f"=K46*{deductible_rate}"
        ws[f"L{row}"] = f"=L46*{deductible_rate}"
        ws[f"M{row}"] = f"=M46*{deductible_rate}"
        for col in ["K", "L", "M"]:
            ws[f"{col}{row}"].style = "currency"

        # Capitalised Interest (Row 48)
        row = 48
        ws[f"J{row}"] = "Capitalised Interest"
        ws[f"K{row}"] = f"=K46-K47"
        ws[f"L{row}"] = f"=L46-L47"
        ws[f"M{row}"] = f"=M46-M47"
        for col in ["K", "L", "M"]:
            ws[f"{col}{row}"].style = "currency"

        # ===== PROPERTY MANAGER STATEMENTS (Rows 50-64) =====

        row = 50
        ws[f"J{row}"] = "Property Manager Statements"
        ws[f"J{row}"].font = Font(bold=True)

        # Headers (Row 51)
        row = 51
        ws[f"K{row}"] = "Rental Income"
        ws[f"L{row}"] = "Agent Fees"
        ws[f"M{row}"] = "Repairs"

        # Monthly PM data (Rows 52-63)
        pm_start_row = 52
        for month_str, row_num in [(m, r+18) for m, r in months]:  # Offset by 18 from interest rows
            if row_num > 63:
                break
            ws[f"I{row_num}"] = month_str
            ws[f"J{row_num}"] = "PM"

            # Add PM data if available
            if month_str in monthly_data:
                if "rental_income_pm" in monthly_data[month_str]:
                    ws[f"K{row_num}"] = float(monthly_data[month_str]["rental_income_pm"])
                    ws[f"K{row_num}"].style = "currency"
                if "agent_fees" in monthly_data[month_str]:
                    ws[f"L{row_num}"] = float(monthly_data[month_str]["agent_fees"])
                    ws[f"L{row_num}"].style = "currency"
                if "repairs_pm" in monthly_data[month_str]:
                    ws[f"M{row_num}"] = float(monthly_data[month_str]["repairs_pm"])
                    ws[f"M{row_num}"].style = "currency"

        # PM Totals (Row 64)
        row = 64
        ws[f"K{row}"] = f"=SUM(K52:K63)"
        ws[f"L{row}"] = f"=SUM(L52:L63)"
        ws[f"M{row}"] = f"=SUM(M52:M63)"
        for col in ["K", "L", "M"]:
            ws[f"{col}{row}"].style = "total"

        # ===== BOTTOM LEFT - DETAIL SECTIONS =====

        # Repairs and Maintenance (Row 50+)
        row = 50
        ws[f"A{row}"] = "Repairs and Maintenance"
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"B{row}"] = "Amount"
        ws[f"C{row}"] = "Date"
        ws[f"D{row}"] = "Invoice"

        repairs_txns = [t for t in transactions if t.category_code == "repairs_maintenance"]
        repairs_txns.sort(key=lambda t: t.transaction_date)

        for i, txn in enumerate(repairs_txns[:5]):  # Show first 5
            row += 1
            ws[f"A{row}"] = txn.description[:30] if txn.description else ""
            ws[f"B{row}"] = float(abs(txn.amount))
            ws[f"B{row}"].style = "currency"
            ws[f"C{row}"] = txn.transaction_date
            ws[f"C{row}"].style = "date_style"
            ws[f"D{row}"] = "Y" if txn.has_receipt else "N"

        # Capital items (Row 56+)
        row = 56
        ws[f"A{row}"] = "Capital"
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"B{row}"] = "Amount"
        ws[f"C{row}"] = "Date"
        ws[f"D{row}"] = "Invoice"

        capital_txns = [t for t in transactions if t.category_code in ["capital", "assets_over_500"]]
        for i, txn in enumerate(capital_txns[:3]):  # Show first 3
            row += 1
            ws[f"A{row}"] = txn.description[:30] if txn.description else ""
            ws[f"B{row}"] = float(abs(txn.amount))
            ws[f"B{row}"].style = "currency"
            ws[f"C{row}"] = txn.transaction_date
            ws[f"C{row}"].style = "date_style"
            ws[f"D{row}"] = "Y" if txn.has_receipt else "N"

        # Notes (Row 64)
        row = 64
        ws[f"A{row}"] = "Notes:"
        ws[f"A{row}"].font = Font(bold=True)

        # Information Source Key (Rows 70-78)
        row = 70
        ws[f"A{row}"] = "Information Source Key"
        ws[f"A{row}"].font = Font(bold=True)

        source_codes = [
            (71, "Additional Information", "AI"),
            (72, "Accounting Fee", "AF"),
            (73, "Bank Statement", "BS"),
            (74, "Client Provided", "CP"),
            (75, "Invoice/Receipt", "INV"),
            (76, "Inland Revenue", "IR"),
            (77, "Property Manager", "PM"),
            (78, "Settlement Statement", "SS"),
        ]

        for row_num, description, code in source_codes:
            ws[f"A{row_num}"] = description
            ws[f"B{row_num}"] = code

    def _build_ird_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the IRD checklist sheet."""

        ws.column_dimensions["A"].width = 60
        ws.column_dimensions["B"].width = 15
        ws.column_dimensions["C"].width = 40

        # Title
        ws["A1"] = "IRD CHECKLIST"
        ws["A1"].font = Font(bold=True, size=14)

        # Headers
        row = 3
        ws[f"A{row}"] = "Question"
        ws[f"B{row}"] = "Answer"
        ws[f"C{row}"] = "Notes"
        for col in ["A", "B", "C"]:
            ws[f"{col}{row}"].font = Font(bold=True)

        # Questions
        questions = [
            "1. Checked WFM/Client File for Notes/Email Correspondence?",
            "2. Checked Account Look Up?",
            "3. Are there any outstanding Income Tax returns?",
            "4. Is there any outstanding Income Tax?",
            "5. Are there any outstanding GST returns?",
            "6. Is there any outstanding GST?",
            "7. Were Provisional Tax Payments made on/before:",
            "   a. 28 August",
            "   b. 15 January",
            "   c. 7 May",
        ]

        row = 4
        for question in questions:
            ws[f"A{row}"] = question
            ws[f"B{row}"] = ""  # Empty for user to fill
            ws[f"C{row}"] = ""  # Empty for user to fill
            row += 1

        # Add some space and notes section
        row += 2
        ws[f"A{row}"] = "Additional Notes:"
        ws[f"A{row}"].font = Font(bold=True)

    def _group_transactions_by_month(self, transactions: List[Transaction]) -> Dict[str, Dict[str, Decimal]]:
        """Group transactions by month and category for the workings sections."""
        monthly_data = defaultdict(lambda: defaultdict(Decimal))

        for txn in transactions:
            if not txn.transaction_date or not txn.category_code:
                continue

            # Format month as "Mon-YY" (e.g., "Apr-24")
            month_key = txn.transaction_date.strftime("%b-%y")

            # Group by relevant categories
            if txn.category_code == "interest":
                monthly_data[month_key]["interest"] += abs(txn.amount)
            elif txn.category_code == "rates":
                monthly_data[month_key]["rates"] += abs(txn.amount)
            elif txn.category_code == "insurance":
                monthly_data[month_key]["insurance"] += abs(txn.amount)
            elif txn.category_code == "bank_fees":
                monthly_data[month_key]["bank_fees"] += abs(txn.amount)
            elif txn.category_code == "rental_income":
                # Check if from PM
                if hasattr(txn, 'source') and txn.source == "PM":
                    monthly_data[month_key]["rental_income_pm"] += abs(txn.amount)
            elif txn.category_code == "agent_fees":
                monthly_data[month_key]["agent_fees"] += abs(txn.amount)
            elif txn.category_code == "repairs_maintenance":
                # Check if from PM
                if hasattr(txn, 'source') and txn.source == "PM":
                    monthly_data[month_key]["repairs_pm"] += abs(txn.amount)

        return dict(monthly_data)

    async def _load_tax_return(self, db: AsyncSession, tax_return_id: UUID) -> TaxReturn:
        """Load tax return with client."""
        result = await db.execute(
            select(TaxReturn)
            .options(selectinload(TaxReturn.client))
            .where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()
        if not tax_return:
            raise ValueError(f"Tax return not found: {tax_return_id}")
        return tax_return

    async def _load_transactions(self, db: AsyncSession, tax_return_id: UUID) -> List[Transaction]:
        """Load all transactions."""
        result = await db.execute(
            select(Transaction)
            .where(Transaction.tax_return_id == tax_return_id)
            .order_by(Transaction.transaction_date)
        )
        return list(result.scalars().all())

    async def _load_summaries(self, db: AsyncSession, tax_return_id: UUID) -> List[TransactionSummary]:
        """Load transaction summaries with category mappings."""
        result = await db.execute(
            select(TransactionSummary)
            .options(selectinload(TransactionSummary.category_mapping))
            .where(TransactionSummary.tax_return_id == tax_return_id)
        )
        return list(result.scalars().all())

    async def _load_pl_mappings(self, db: AsyncSession) -> List[PLRowMapping]:
        """Load P&L mappings."""
        result = await db.execute(
            select(PLRowMapping).order_by(PLRowMapping.sort_order)
        )
        return list(result.scalars().all())


# Singleton
_workbook_generator: Optional[WorkbookGenerator] = None


def get_workbook_generator() -> WorkbookGenerator:
    """Get singleton workbook generator."""
    global _workbook_generator
    if _workbook_generator is None:
        _workbook_generator = WorkbookGenerator()
    return _workbook_generator