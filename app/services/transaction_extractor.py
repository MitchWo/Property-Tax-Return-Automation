"""Transaction extractor service for parsing bank statements and other documents."""
import csv
import io
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.models.db_models import Document, TaxReturn
from app.rules.loader import get_bank_parser, load_bank_parsers
from app.schemas.transactions import (
    DocumentExtractionResult,
    ExtractedTransaction,
    SettlementExtraction,
)
from app.services.phase1_document_intake.claude_client import ClaudeClient
from app.services.skill_loader import get_skill_loader

logger = logging.getLogger(__name__)


class TransactionExtractor:
    """Extract transactions from various document types."""

    def __init__(self):
        """Initialize transaction extractor."""
        self.claude_client = ClaudeClient()
        self.skill_loader = get_skill_loader()

    async def extract_from_document(
        self,
        document: Document,
        tax_return: TaxReturn,
        file_content: bytes = None,
        text_content: str = None,
        image_data: List[Tuple[bytes, str]] = None
    ) -> DocumentExtractionResult:
        """
        Extract transactions from a document based on its type.

        Args:
            document: Document record from database
            tax_return: Associated tax return
            file_content: Raw file bytes (for CSV parsing)
            text_content: Extracted text content
            image_data: List of (image_bytes, media_type) for vision

        Returns:
            DocumentExtractionResult with extracted transactions
        """
        doc_type = document.document_type

        context = {
            "property_address": tax_return.property_address,
            "tax_year": tax_return.tax_year,
            "property_type": tax_return.property_type.value,
            "year_of_ownership": tax_return.year_of_ownership,
            "gst_registered": tax_return.gst_registered
        }

        # Route to appropriate extractor
        if doc_type == "bank_statement":
            return await self._extract_bank_statement(
                document, context, file_content, text_content, image_data
            )
        elif doc_type == "loan_statement":
            return await self._extract_loan_statement(
                document, context, text_content, image_data
            )
        elif doc_type == "property_manager_statement":
            return await self._extract_pm_statement(
                document, context, text_content, image_data
            )
        elif doc_type == "settlement_statement":
            return await self._extract_settlement_statement(
                document, context, text_content, image_data
            )
        else:
            # For other document types, try generic extraction
            return await self._extract_generic(
                document, context, text_content, image_data
            )

    async def _extract_bank_statement(
        self,
        document: Document,
        context: Dict,
        file_content: bytes = None,
        text_content: str = None,
        image_data: List[Tuple[bytes, str]] = None
    ) -> DocumentExtractionResult:
        """Extract transactions from bank statement."""
        transactions = []
        extraction_method = "unknown"
        errors = []
        warnings = []

        # Try CSV parsing first (fastest, most accurate)
        if file_content and document.original_filename.lower().endswith('.csv'):
            try:
                transactions, parse_warnings = self._parse_bank_csv(
                    file_content, document.original_filename, context
                )
                extraction_method = "csv_parser"
                warnings.extend(parse_warnings)
                logger.info(f"Parsed {len(transactions)} transactions from CSV")
            except Exception as e:
                logger.warning(f"CSV parsing failed: {e}")
                errors.append(f"CSV parsing failed: {str(e)}")

        # If no transactions yet, try text extraction
        if not transactions and text_content:
            try:
                transactions, text_warnings = self._parse_bank_text(
                    text_content, context
                )
                extraction_method = "text_parser"
                warnings.extend(text_warnings)
                logger.info(f"Parsed {len(transactions)} transactions from text")
            except Exception as e:
                logger.warning(f"Text parsing failed: {e}")
                errors.append(f"Text parsing failed: {str(e)}")

        # If still no transactions, use Claude vision
        if not transactions and image_data:
            try:
                transactions, claude_warnings = await self._extract_with_claude(
                    context, text_content, image_data, "bank_statement"
                )
                extraction_method = "claude_vision"
                warnings.extend(claude_warnings)
                logger.info(f"Extracted {len(transactions)} transactions via Claude")
            except Exception as e:
                logger.error(f"Claude extraction failed: {e}")
                errors.append(f"Claude extraction failed: {str(e)}")

        # Calculate totals
        total_income = Decimal("0")
        total_expenses = Decimal("0")

        for txn in transactions:
            if txn.amount > 0:
                total_income += txn.amount
            else:
                total_expenses += txn.amount

        return DocumentExtractionResult(
            document_id=document.id,
            document_type=document.document_type,
            filename=document.original_filename,
            transactions=transactions,
            extraction_method=extraction_method,
            total_transactions=len(transactions),
            total_income=total_income,
            total_expenses=abs(total_expenses),
            errors=errors,
            warnings=warnings
        )

    def _parse_bank_csv(
        self,
        file_content: bytes,
        filename: str,
        context: Dict
    ) -> Tuple[List[ExtractedTransaction], List[str]]:
        """
        Parse transactions from bank CSV export.

        Args:
            file_content: Raw CSV bytes
            filename: Original filename (for bank detection)
            context: Tax return context

        Returns:
            Tuple of (transactions, warnings)
        """
        transactions = []
        warnings = []

        # Log CSV parsing start
        logger.info(f"Starting CSV parse for file: {filename}")
        logger.debug(f"File size: {len(file_content)} bytes")

        # Show preview of CSV content
        try:
            preview = file_content[:500].decode('utf-8', errors='ignore')
            logger.debug(f"CSV content preview:\n{preview}")
        except Exception as e:
            logger.warning(f"Could not preview CSV content: {e}")

        # Detect bank from filename
        bank_parser = self._detect_bank_parser(filename, file_content)

        if not bank_parser:
            warnings.append("Could not detect bank format, using generic parser")
            logger.warning(f"No bank parser detected for {filename}, using generic")
            bank_parser = load_bank_parsers().get("generic", {})
        else:
            bank_name = bank_parser.get('name', 'unknown')
            logger.info(f"Detected bank parser: {bank_name}")

        csv_config = bank_parser.get("csv_format", {})
        amount_style = bank_parser.get("amount_style", "single")

        logger.info(f"Using amount_style: {amount_style} for bank parser: {bank_parser.get('name', 'unknown')}")
        logger.debug(f"CSV config: delimiter='{csv_config.get('delimiter', ',')}', "
                    f"encoding='{csv_config.get('encoding', 'utf-8')}', "
                    f"has_header={csv_config.get('has_header', True)}, "
                    f"amount_style={amount_style}")

        # Decode content
        encoding = csv_config.get("encoding", "utf-8")
        try:
            content_str = file_content.decode(encoding)
        except UnicodeDecodeError:
            content_str = file_content.decode("latin-1")
            warnings.append("Fell back to latin-1 encoding")

        # Parse CSV
        delimiter = csv_config.get("delimiter", ",")
        reader = csv.reader(io.StringIO(content_str), delimiter=delimiter)

        rows = list(reader)

        logger.info(f"Parsed {len(rows)} rows from CSV")

        # Skip header if present
        start_row = 1 if csv_config.get("has_header", True) else 0

        # Show CSV structure - display first several rows to understand the format
        if rows:
            for i in range(min(10, len(rows))):
                logger.info(f"Row {i}: {rows[i]}")

        # For BNZ, check if first row is account number and skip to actual headers
        if bank_parser.get('name') == 'Bank of New Zealand' and rows:
            # BNZ exports often have account number on first row, then headers
            if rows[0] and rows[0][0] and '38-' in rows[0][0]:
                logger.info("BNZ format detected - skipping account number row")
                start_row = 2  # Skip both account row and header row
            elif len(rows) > 1 and 'Date' in str(rows[1]):
                start_row = 2  # Headers are on row 1, data starts on row 2

        if len(rows) > start_row:
            logger.debug(f"First data row (row {start_row}): {rows[start_row] if len(rows) > start_row else 'NO DATA'}")
            if len(rows) > start_row + 1:
                logger.debug(f"Second data row (row {start_row + 1}): {rows[start_row + 1]}")

        # Get tax year date range for filtering
        tax_year = context.get("tax_year", "FY25")
        year = int(tax_year[2:]) + 2000
        tax_year_start = date(year - 1, 4, 1)
        tax_year_end = date(year, 3, 31)

        logger.info(f"Tax year filter: {tax_year_start} to {tax_year_end}")

        # Column indices
        date_col = csv_config.get("date_column", 0)
        desc_col = csv_config.get("description_column", 1)
        other_party_col = csv_config.get("other_party_column")
        balance_col = csv_config.get("balance_column")

        logger.info(f"Column config: date_col={date_col}, desc_col={desc_col}, "
                   f"other_party_col={other_party_col}, balance_col={balance_col}")

        date_format = csv_config.get("date_format", "%d/%m/%Y")

        # Track statistics
        rows_processed = 0
        dates_parsed = 0
        dates_failed = 0
        out_of_range_dates = 0
        zero_amounts = 0
        amount_parse_failures = 0

        for row_num, row in enumerate(rows[start_row:], start=start_row + 1):
            try:
                rows_processed += 1

                if len(row) < 3:
                    continue

                # Parse date
                date_str = row[date_col].strip()
                txn_date = None

                # Try the configured format first
                try:
                    txn_date = datetime.strptime(date_str, date_format).date()
                    dates_parsed += 1
                    logger.debug(f"Row {row_num}: Parsed date '{date_str}' using configured format {date_format}")
                except ValueError:
                    # Try alternative formats including D-Mon-YY format
                    alt_formats = [
                        "%d-%b-%y",      # 27-Jul-23, 3-Aug-23 (NEW)
                        "%d-%b-%Y",      # 27-Jul-2023
                        "%d/%m/%Y",      # 27/07/2023
                        "%d/%m/%y",      # 27/07/23
                        "%Y/%m/%d",      # 2023/07/27 (ASB)
                        "%Y-%m-%d",      # 2023-07-27 (ISO)
                        "%d-%m-%Y",      # 27-07-2023
                        "%d-%m-%y",      # 27-07-23
                        "%d %b %Y",      # 27 Jul 2023
                        "%d %b %y",      # 27 Jul 23
                    ]

                    for alt_format in alt_formats:
                        try:
                            txn_date = datetime.strptime(date_str, alt_format).date()
                            dates_parsed += 1
                            logger.debug(f"Row {row_num}: Parsed date '{date_str}' using format {alt_format}")
                            break
                        except ValueError:
                            continue

                    if not txn_date:
                        dates_failed += 1
                        warnings.append(f"Row {row_num}: Could not parse date '{date_str}'")
                        logger.warning(f"Row {row_num}: Failed to parse date '{date_str}' - tried all formats")
                        continue

                # Filter by tax year
                if txn_date < tax_year_start or txn_date > tax_year_end:
                    out_of_range_dates += 1
                    logger.debug(f"Row {row_num}: Date {txn_date} outside tax year range {tax_year_start} to {tax_year_end}")
                    continue

                # Log raw values for debugging
                logger.debug(f"Row {row_num}: Processing - date='{row[date_col] if len(row) > date_col else 'N/A'}', "
                           f"desc='{row[desc_col][:50] if len(row) > desc_col else 'N/A'}...'")

                # Parse amount
                if amount_style == "split":
                    debit_col = csv_config.get("debit_column", 2)
                    credit_col = csv_config.get("credit_column", 3)

                    debit_str = row[debit_col] if len(row) > debit_col else ""
                    credit_str = row[credit_col] if len(row) > credit_col else ""

                    logger.debug(f"Row {row_num}: Split amounts - debit='{debit_str}', credit='{credit_str}'")

                    debit = self._parse_amount(debit_str)
                    credit = self._parse_amount(credit_str)

                    if credit and credit > 0:
                        amount = credit
                    elif debit and debit > 0:
                        amount = -debit
                    else:
                        amount = Decimal("0")
                        logger.warning(f"Row {row_num}: Both debit and credit are zero or None")
                else:
                    amount_col = csv_config.get("amount_column", 2)
                    amount_str = row[amount_col] if len(row) > amount_col else "0"
                    logger.debug(f"Row {row_num}: Single amount column - raw='{amount_str}'")

                    amount = self._parse_amount(amount_str)
                    if amount is None:
                        logger.warning(f"Row {row_num}: Could not parse amount '{amount_str}'")
                        amount_parse_failures += 1
                        amount = Decimal("0")

                if amount == Decimal("0"):
                    logger.debug(f"Row {row_num}: Skipping - amount is zero")
                    zero_amounts += 1
                    continue

                # Get description
                description = row[desc_col].strip() if len(row) > desc_col else ""

                # Get other party
                other_party = None
                if other_party_col is not None and len(row) > other_party_col:
                    other_party = row[other_party_col].strip() or None

                # Get balance
                balance = None
                if balance_col is not None and len(row) > balance_col:
                    balance = self._parse_amount(row[balance_col])

                # Create transaction
                txn = ExtractedTransaction(
                    transaction_date=txn_date,
                    description=description,
                    other_party=other_party,
                    amount=amount,
                    balance=balance,
                    confidence=0.95,  # High confidence for CSV parsing
                    row_number=row_num,
                    raw_data={"row": row}
                )

                transactions.append(txn)
                logger.info(f"Row {row_num}: Created transaction - date={txn_date}, amount={amount}, desc='{description[:30]}...')")

            except Exception as e:
                warnings.append(f"Row {row_num}: Error parsing - {str(e)}")
                logger.debug(f"Row {row_num} parse error: {e}")
                continue

        # Log parsing summary
        logger.info(f"CSV parsing complete: {len(transactions)} transactions extracted from {rows_processed} rows")
        logger.info(f"Date parsing: {dates_parsed} successful, {dates_failed} failed, {out_of_range_dates} out of tax year range")
        logger.info(f"Amount parsing: {zero_amounts} zero amounts, {amount_parse_failures} parse failures")

        if transactions:
            logger.debug(f"Sample transaction: date={transactions[0].transaction_date}, "
                        f"amount={transactions[0].amount}, desc={transactions[0].description[:50]}")
        elif zero_amounts > 0:
            logger.warning(f"No transactions extracted. All {zero_amounts} rows had zero amounts.")
        elif dates_failed > 0:
            logger.warning(f"No transactions extracted. {dates_failed} dates could not be parsed. Check date format.")
        elif out_of_range_dates > 0:
            logger.warning(f"No transactions extracted. All {out_of_range_dates} parsed dates were outside tax year range.")

        return transactions, warnings

    def _parse_bank_text(
        self,
        text_content: str,
        context: Dict
    ) -> Tuple[List[ExtractedTransaction], List[str]]:
        """
        Parse transactions from extracted text (PDF text extraction).

        This is a fallback for when CSV is not available but text was extracted.
        """
        transactions = []
        warnings = []

        # Get tax year date range
        tax_year = context.get("tax_year", "FY25")
        year = int(tax_year[2:]) + 2000
        tax_year_start = date(year - 1, 4, 1)
        tax_year_end = date(year, 3, 31)

        # Common patterns for NZ bank statements
        # Pattern: DATE DESCRIPTION AMOUNT BALANCE
        date_patterns = [
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',  # DD/MM/YYYY or DD-MM-YYYY
            r'(\d{4}[/-]\d{1,2}[/-]\d{1,2})',     # YYYY/MM/DD
        ]

        amount_pattern = r'(-?\$?[\d,]+\.?\d{0,2})'

        lines = text_content.split('\n')

        for line_num, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue

            # Try to find a date
            txn_date = None
            for date_pattern in date_patterns:
                date_match = re.search(date_pattern, line)
                if date_match:
                    date_str = date_match.group(1)
                    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d",
                               "%d/%m/%y", "%d-%m-%y"]:
                        try:
                            txn_date = datetime.strptime(date_str, fmt).date()
                            break
                        except ValueError:
                            continue
                    if txn_date:
                        break

            if not txn_date:
                continue

            # Filter by tax year
            if txn_date < tax_year_start or txn_date > tax_year_end:
                continue

            # Find amounts in line
            amounts = re.findall(amount_pattern, line)
            if not amounts:
                continue

            # Try to parse amounts
            parsed_amounts = []
            for amt_str in amounts:
                amt = self._parse_amount(amt_str)
                if amt is not None and amt != Decimal("0"):
                    parsed_amounts.append(amt)

            if not parsed_amounts:
                continue

            # Use last non-balance amount as transaction amount
            # (Balance is usually last, transaction amount second-to-last)
            amount = parsed_amounts[-2] if len(parsed_amounts) >= 2 else parsed_amounts[-1]
            balance = parsed_amounts[-1] if len(parsed_amounts) >= 2 else None

            # Extract description (everything between date and first amount)
            description = line
            if date_match:
                description = line[date_match.end():].strip()
            # Remove amounts from description
            for amt_str in amounts:
                description = description.replace(amt_str, '').strip()

            if not description:
                description = "Unknown transaction"

            txn = ExtractedTransaction(
                transaction_date=txn_date,
                description=description[:200],  # Limit length
                amount=amount,
                balance=balance,
                confidence=0.70,  # Lower confidence for text parsing
                needs_review=True,
                review_reason="Extracted from text - verify accuracy",
                row_number=line_num,
                raw_data={"line": line}
            )

            transactions.append(txn)

        if not transactions:
            warnings.append("No transactions found in text content")

        return transactions, warnings

    async def _extract_with_claude(
        self,
        context: Dict,
        text_content: str,
        image_data: List[Tuple[bytes, str]],
        doc_type: str
    ) -> Tuple[List[ExtractedTransaction], List[str]]:
        """
        Extract transactions using Claude vision.

        Used for scanned PDFs and complex documents.
        """
        transactions = []
        warnings = []

        # Get appropriate prompt
        if doc_type == "bank_statement":
            prompt = self.skill_loader.get_bank_statement_prompt(context)
        elif doc_type == "property_manager_statement":
            prompt = self.skill_loader.get_pm_statement_prompt(context)
        else:
            prompt = self.skill_loader.get_bank_statement_prompt(context)

        # Add domain knowledge
        domain_context = self.skill_loader.get_domain_context()
        full_prompt = f"{domain_context}\n\n{prompt}"

        try:
            # Call Claude
            response = await self.claude_client.extract_transactions_with_vision(
                full_prompt, text_content, image_data
            )

            # Parse response
            if isinstance(response, dict):
                raw_transactions = response.get("transactions", [])
                warnings.extend(response.get("warnings", []))

                for raw_txn in raw_transactions:
                    try:
                        # Parse date
                        date_str = raw_txn.get("date", "")
                        txn_date = datetime.strptime(date_str, "%Y-%m-%d").date()

                        # Parse amount
                        amount = Decimal(str(raw_txn.get("amount", 0)))

                        txn = ExtractedTransaction(
                            transaction_date=txn_date,
                            description=raw_txn.get("description", ""),
                            other_party=raw_txn.get("other_party"),
                            amount=amount,
                            balance=Decimal(str(raw_txn["balance"])) if raw_txn.get("balance") else None,
                            suggested_category=raw_txn.get("suggested_category"),
                            confidence=raw_txn.get("confidence", 0.80),
                            needs_review=raw_txn.get("needs_review", False),
                            review_reason=raw_txn.get("review_reason"),
                            row_number=raw_txn.get("row_number"),
                            raw_data=raw_txn
                        )
                        transactions.append(txn)

                    except Exception as e:
                        warnings.append(f"Failed to parse transaction: {e}")
                        continue

        except Exception as e:
            logger.error(f"Claude extraction failed: {e}")
            warnings.append(f"Claude extraction error: {str(e)}")

        return transactions, warnings

    async def _extract_loan_statement(
        self,
        document: Document,
        context: Dict,
        text_content: str = None,
        image_data: List[Tuple[bytes, str]] = None
    ) -> DocumentExtractionResult:
        """
        Extract transactions from loan statements.

        Loan statements typically show:
        - Interest charged (deductible expense)
        - Principal repayments (NOT deductible - excluded)
        - Account/facility fees (deductible)
        """
        if not text_content:
            return DocumentExtractionResult(
                document_id=document.id,
                document_type=document.document_type,
                filename=document.original_filename,
                transactions=[],
                extraction_method="loan_statement",
                total_transactions=0,
                total_income=Decimal("0"),
                total_expenses=Decimal("0"),
                errors=["No text content to extract"],
                warnings=[]
            )

        # Try CSV extraction for loan statements
        # Many loan statements are exported as CSV with similar format to bank statements
        csv_result = await self._extract_from_csv(text_content, document, context)

        if csv_result and csv_result.transactions:
            # Filter and categorize loan-specific transactions
            filtered_transactions = []
            for txn in csv_result.transactions:
                # Auto-categorize common loan transaction types
                desc_lower = txn.description.lower()

                # Interest charges - highly deductible
                if any(keyword in desc_lower for keyword in ['interest', 'int charged', 'mortgage int']):
                    txn.suggested_category = 'interest'
                    txn.confidence = 0.95
                    txn.needs_review = False
                    filtered_transactions.append(txn)

                # Facility/account fees - deductible
                elif any(keyword in desc_lower for keyword in ['facility fee', 'account fee', 'service fee', 'admin fee']):
                    txn.suggested_category = 'bank_fees'
                    txn.confidence = 0.90
                    txn.needs_review = False
                    filtered_transactions.append(txn)

                # Principal repayments - NOT deductible, exclude
                elif any(keyword in desc_lower for keyword in ['principal', 'repayment', 'payment received', 'credit']):
                    # Skip principal repayments - they're not tax deductible
                    logger.info(f"Excluding principal payment: {txn.description}")
                    continue

                # Unknown - include for review
                else:
                    txn.needs_review = True
                    txn.review_reason = "Loan transaction type needs verification"
                    filtered_transactions.append(txn)

            # Update result with filtered transactions
            csv_result.transactions = filtered_transactions
            csv_result.total_transactions = len(filtered_transactions)
            csv_result.extraction_method = "loan_csv"

            if not csv_result.warnings:
                csv_result.warnings = []
            csv_result.warnings.append("Principal repayments excluded (not tax deductible)")

            return csv_result

        # If CSV extraction failed, return empty result with warning
        return DocumentExtractionResult(
            document_id=document.id,
            document_type=document.document_type,
            filename=document.original_filename,
            transactions=[],
            extraction_method="loan_statement",
            total_transactions=0,
            total_income=Decimal("0"),
            total_expenses=Decimal("0"),
            errors=["Could not extract transactions from loan statement"],
            warnings=["Upload loan statement as CSV for best results"]
        )

    async def _extract_pm_statement(
        self,
        document: Document,
        context: Dict,
        text_content: str = None,
        image_data: List[Tuple[bytes, str]] = None
    ) -> DocumentExtractionResult:
        """Extract transactions from property manager statement."""
        transactions = []
        warnings = []
        errors = []

        # PM statements need Claude for extraction (complex format)
        if image_data or text_content:
            try:
                transactions, extract_warnings = await self._extract_with_claude(
                    context, text_content, image_data, "property_manager_statement"
                )
                warnings.extend(extract_warnings)
            except Exception as e:
                errors.append(f"PM statement extraction failed: {str(e)}")
        else:
            errors.append("No content available for PM statement extraction")

        # Calculate totals
        total_income = Decimal("0")
        total_expenses = Decimal("0")

        for txn in transactions:
            if txn.amount > 0:
                total_income += txn.amount
            else:
                total_expenses += txn.amount

        return DocumentExtractionResult(
            document_id=document.id,
            document_type=document.document_type,
            filename=document.original_filename,
            transactions=transactions,
            extraction_method="claude_vision" if image_data else "claude_text",
            total_transactions=len(transactions),
            total_income=total_income,
            total_expenses=abs(total_expenses),
            errors=errors,
            warnings=warnings
        )

    async def _extract_settlement_statement(
        self,
        document: Document,
        context: Dict,
        text_content: str = None,
        image_data: List[Tuple[bytes, str]] = None
    ) -> DocumentExtractionResult:
        """
        Extract Year 1 data from settlement statement.

        Settlement statements contain apportionments, not transactions.
        We convert them to synthetic transactions for the P&L.
        """
        transactions = []
        warnings = []
        errors = []

        # Settlement statements require Claude
        prompt = self.skill_loader.get_settlement_prompt(context)
        domain_context = self.skill_loader.get_domain_context()
        full_prompt = f"{domain_context}\n\n{prompt}"

        try:
            response = await self.claude_client.extract_settlement_with_vision(
                full_prompt, text_content, image_data
            )

            if isinstance(response, dict):
                settlement_date_str = response.get("settlement_details", {}).get("settlement_date")
                settlement_date = datetime.strptime(settlement_date_str, "%Y-%m-%d").date() if settlement_date_str else date.today()

                apportionments = response.get("apportionments", {})
                other_items = response.get("other_items", {})

                # Create synthetic transactions for each apportionment

                # Rates apportionment
                if "rates" in apportionments:
                    rates_data = apportionments["rates"]
                    amount = Decimal(str(rates_data.get("amount", 0)))
                    if amount > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description=f"Settlement - Rates Apportionment: {rates_data.get('description', '')}",
                            amount=-amount,  # Expense
                            suggested_category="rates",
                            confidence=0.95,
                            raw_data={"source": "settlement", "type": "rates_apportionment"}
                        ))

                # Rates vendor credit (reduces expense)
                if "rates_vendor_credit" in apportionments:
                    credit_data = apportionments["rates_vendor_credit"]
                    amount = abs(Decimal(str(credit_data.get("amount", 0))))
                    if amount > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description="Settlement - Vendor Credit for Rates",
                            amount=amount,  # Positive - reduces rates expense
                            suggested_category="rates",
                            confidence=0.95,
                            needs_review=True,
                            review_reason="Vendor credit - verify this reduces rates expense",
                            raw_data={"source": "settlement", "type": "rates_vendor_credit"}
                        ))

                # Body corporate
                if "body_corporate" in apportionments:
                    bc_data = apportionments["body_corporate"]
                    operating = Decimal(str(bc_data.get("operating_fund", bc_data.get("deductible", 0))))
                    if operating > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description="Settlement - Body Corporate (Operating Fund)",
                            amount=-operating,
                            suggested_category="body_corporate",
                            confidence=0.95,
                            raw_data={"source": "settlement", "type": "body_corporate"}
                        ))

                # Resident society
                if "resident_society" in apportionments:
                    rs_data = apportionments["resident_society"]
                    amount = Decimal(str(rs_data.get("amount", 0)))
                    if amount > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description="Settlement - Resident Society Levy",
                            amount=-amount,
                            suggested_category="resident_society",
                            confidence=0.95,
                            raw_data={"source": "settlement", "type": "resident_society"}
                        ))

                # Water rates
                if "water_rates" in apportionments:
                    water_data = apportionments["water_rates"]
                    amount = Decimal(str(water_data.get("amount", 0)))
                    if amount > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description="Settlement - Water Rates Apportionment",
                            amount=-amount,
                            suggested_category="water_rates",
                            confidence=0.95,
                            raw_data={"source": "settlement", "type": "water_rates"}
                        ))

                # Legal fees
                if "legal_fees" in other_items:
                    legal_data = other_items["legal_fees"]
                    amount = Decimal(str(legal_data.get("amount", 0)))
                    if amount > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description="Settlement - Legal Fees (Conveyancing)",
                            amount=-amount,
                            suggested_category="legal_fees",
                            confidence=0.95,
                            raw_data={"source": "settlement", "type": "legal_fees"}
                        ))

                # Interest on deposit (nets against interest expense)
                if "interest_on_deposit" in other_items:
                    interest_data = other_items["interest_on_deposit"]
                    amount = Decimal(str(interest_data.get("amount", 0)))
                    if amount > 0:
                        transactions.append(ExtractedTransaction(
                            transaction_date=settlement_date,
                            description="Settlement - Interest on Deposit (Credit)",
                            amount=amount,  # Positive - reduces interest expense
                            suggested_category="interest",
                            confidence=0.90,
                            needs_review=True,
                            review_reason="Interest credit from deposit - verify nets against interest expense",
                            raw_data={"source": "settlement", "type": "interest_on_deposit"}
                        ))

                warnings.extend(response.get("warnings", []))

        except Exception as e:
            logger.error(f"Settlement extraction failed: {e}")
            errors.append(f"Settlement extraction failed: {str(e)}")

        # Calculate totals
        total_income = Decimal("0")
        total_expenses = Decimal("0")

        for txn in transactions:
            if txn.amount > 0:
                total_income += txn.amount
            else:
                total_expenses += txn.amount

        return DocumentExtractionResult(
            document_id=document.id,
            document_type=document.document_type,
            filename=document.original_filename,
            transactions=transactions,
            extraction_method="claude_settlement",
            total_transactions=len(transactions),
            total_income=total_income,
            total_expenses=abs(total_expenses),
            errors=errors,
            warnings=warnings
        )

    async def _extract_generic(
        self,
        document: Document,
        context: Dict,
        text_content: str = None,
        image_data: List[Tuple[bytes, str]] = None
    ) -> DocumentExtractionResult:
        """Generic extraction for other document types."""
        # For now, return empty result
        # Future: could extract invoice amounts, etc.
        return DocumentExtractionResult(
            document_id=document.id,
            document_type=document.document_type,
            filename=document.original_filename,
            transactions=[],
            extraction_method="skipped",
            total_transactions=0,
            total_income=Decimal("0"),
            total_expenses=Decimal("0"),
            errors=[],
            warnings=[f"Document type '{document.document_type}' does not contain transactions"]
        )

    def _detect_bank_parser(
        self,
        filename: str,
        content: bytes = None
    ) -> Optional[Dict]:
        """Detect which bank parser to use based on filename or content."""
        filename_lower = filename.lower()

        # Check filename for bank identifiers
        bank_keywords = {
            "asb": "asb",
            "anz": "anz",
            "kiwibank": "kiwibank",
            "westpac": "westpac",
            "bnz": "bnz",
            "tsb": "tsb",
            "cooperative": "cooperative",
            "co-op": "cooperative"
        }

        for keyword, bank_key in bank_keywords.items():
            if keyword in filename_lower:
                return get_bank_parser(bank_key)

        # Try to detect from content
        if content:
            try:
                content_str = content.decode("utf-8", errors='ignore')[:2000]  # Check first 2000 chars
                content_lower = content_str.lower()

                # Check for bank keywords in content
                for keyword, bank_key in bank_keywords.items():
                    if keyword in content_lower:
                        logger.info(f"Detected {bank_key} from content keyword")
                        return get_bank_parser(bank_key)

                # Check for BNZ account number pattern (38-xxxx-xxxxxxx-xx)
                if "38-" in content_str[:100] or "38-9024" in content_str[:100]:
                    logger.info("Detected BNZ from account number pattern")
                    return get_bank_parser("bnz")

            except Exception as e:
                logger.debug(f"Error detecting bank from content: {e}")
                pass

        return None

    def _parse_amount(self, amount_str: str) -> Optional[Decimal]:
        """Parse amount string to Decimal."""
        if not amount_str:
            return None

        # Clean the string
        cleaned = amount_str.strip()
        cleaned = cleaned.replace('$', '').replace(',', '').replace(' ', '')

        # Handle parentheses for negative (accounting format)
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = '-' + cleaned[1:-1]

        # Handle DR/CR suffixes
        if cleaned.upper().endswith('DR'):
            cleaned = '-' + cleaned[:-2]
        elif cleaned.upper().endswith('CR'):
            cleaned = cleaned[:-2]

        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None