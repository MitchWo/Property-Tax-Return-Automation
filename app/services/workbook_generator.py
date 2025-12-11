"""Workbook generator service for creating IR3R Excel workbooks."""
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import (
    Alignment,
    Border,
    Font,
    NamedStyle,
    PatternFill,
    Side,
)
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

    # Header style - dark blue
    header_style = NamedStyle(name="header")
    header_style.font = Font(bold=True, size=11, color="FFFFFF")
    header_style.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_style.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    header_style.border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )
    wb.add_named_style(header_style)

    # Sub-header style - light blue
    subheader_style = NamedStyle(name="subheader")
    subheader_style.font = Font(bold=True, size=10)
    subheader_style.fill = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")
    subheader_style.alignment = Alignment(horizontal="center", vertical="center")
    subheader_style.border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin")
    )
    wb.add_named_style(subheader_style)

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
    percent_style.number_format = '0.00%'
    percent_style.alignment = Alignment(horizontal="right")
    wb.add_named_style(percent_style)

    # Date style
    date_style = NamedStyle(name="date_style")
    date_style.number_format = 'DD/MM/YYYY'
    date_style.alignment = Alignment(horizontal="center")
    wb.add_named_style(date_style)

    # Section header - medium blue
    section_style = NamedStyle(name="section")
    section_style.font = Font(bold=True, size=11, color="1F4E79")
    section_style.fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
    section_style.border = Border(
        bottom=Side(style="medium", color="4472C4")
    )
    wb.add_named_style(section_style)

    # Total row
    total_style = NamedStyle(name="total")
    total_style.font = Font(bold=True)
    total_style.border = Border(top=Side(style="thin"), bottom=Side(style="double"))
    total_style.number_format = '"$"#,##0.00'
    wb.add_named_style(total_style)

    # Subtotal row
    subtotal_style = NamedStyle(name="subtotal")
    subtotal_style.font = Font(bold=True)
    subtotal_style.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    subtotal_style.number_format = '"$"#,##0.00'
    wb.add_named_style(subtotal_style)

    # Income (green)
    income_style = NamedStyle(name="income")
    income_style.font = Font(color="006400")
    income_style.number_format = '"$"#,##0.00'
    wb.add_named_style(income_style)

    # Expense (dark red)
    expense_style = NamedStyle(name="expense")
    expense_style.font = Font(color="8B0000")
    expense_style.number_format = '"$"#,##0.00'
    wb.add_named_style(expense_style)

    # Link style (for formula references)
    link_style = NamedStyle(name="link")
    link_style.font = Font(color="0563C1", underline="single")
    link_style.number_format = '"$"#,##0.00'
    wb.add_named_style(link_style)

    # Note style
    note_style = NamedStyle(name="note")
    note_style.font = Font(italic=True, color="666666", size=9)
    wb.add_named_style(note_style)


class WorkbookGenerator:
    """Generate IR3R Excel workbooks from processed transactions."""

    def __init__(self):
        """Initialize workbook generator."""
        self.tax_rules_service = get_tax_rules_service()
        self.output_dir = settings.UPLOAD_DIR / "workbooks"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Track row references for P&L formulas
        self.sheet_references = {}

    async def generate_workbook(
        self,
        db: AsyncSession,
        tax_return_id: UUID
    ) -> Path:
        """
        Generate complete IR3R workbook for a tax return.

        Args:
            db: Database session
            tax_return_id: Tax return to generate workbook for

        Returns:
            Path to generated workbook file
        """
        # Reset references
        self.sheet_references = {}

        # Load all data
        tax_return = await self._load_tax_return(db, tax_return_id)
        transactions = await self._load_transactions(db, tax_return_id)
        summaries = await self._load_summaries(db, tax_return_id)
        pl_mappings = await self._load_pl_mappings(db)

        # Get tax rules
        interest_deductibility = await self.tax_rules_service.get_interest_deductibility(
            db, tax_return.tax_year, tax_return.property_type.value
        )
        accounting_fee = await self.tax_rules_service.get_accounting_fee(db)

        # Create workbook
        wb = Workbook()
        create_styles(wb)

        # Remove default sheet
        default_sheet = wb.active

        # Build context
        context = {
            "tax_return": tax_return,
            "transactions": transactions,
            "summaries": summaries,
            "pl_mappings": pl_mappings,
            "interest_deductibility": interest_deductibility,
            "accounting_fee": accounting_fee
        }

        # Create tabs in order (P&L will be first but built last)
        pl_sheet = wb.create_sheet("P&L", 0)
        rental_bs_sheet = wb.create_sheet("Rental BS", 1)
        interest_sheet = wb.create_sheet("Interest Workings", 2)

        # Check if we have PM statement transactions
        pm_transactions = [t for t in transactions
                         if hasattr(t, 'raw_data') and t.raw_data and t.raw_data.get("source") == "pm_statement"]
        pm_sheet = None
        if pm_transactions or any(s.category_code == "agent_fees" for s in summaries):
            pm_sheet = wb.create_sheet("PM Statements", 3)

        # Year 1 settlement tab
        settlement_sheet = None
        if tax_return.year_of_ownership == 1:
            settlement_sheet = wb.create_sheet("Settlement", 4 if pm_sheet else 3)

        # Depreciation tab
        depreciation_sheet = None
        dep_summary = next((s for s in summaries if s.category_code == "depreciation"), None)
        if dep_summary:
            idx = 5 if pm_sheet and settlement_sheet else (4 if pm_sheet or settlement_sheet else 3)
            depreciation_sheet = wb.create_sheet("Depreciation", idx)

        # Remove default sheet
        if default_sheet and default_sheet.title == "Sheet":
            wb.remove(default_sheet)

        # Build sheets (order matters - build referenced sheets first)
        self._build_rental_bs_sheet(rental_bs_sheet, context)
        self._build_interest_sheet(interest_sheet, context)

        if pm_sheet:
            self._build_pm_sheet(pm_sheet, context)

        if settlement_sheet:
            self._build_settlement_sheet(settlement_sheet, context)

        if depreciation_sheet:
            self._build_depreciation_sheet(depreciation_sheet, context)

        # Build P&L last (uses references from other sheets)
        self._build_pl_sheet(pl_sheet, context)

        # Set P&L as active
        wb.active = pl_sheet

        # Generate filename
        client_name = self._sanitize_filename(tax_return.client.name)
        address_short = self._sanitize_filename(tax_return.property_address.split(",")[0][:20])
        filename = f"{client_name}_{address_short}_{tax_return.tax_year}_IR3R.xlsx"
        filepath = self.output_dir / filename

        # Save
        wb.save(filepath)
        logger.info(f"Generated workbook: {filepath}")

        return filepath

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize string for use in filename."""
        return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).replace(" ", "_")

    def _build_pl_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the P&L (Profit & Loss) sheet with formula references."""
        tax_return = context["tax_return"]
        summaries = context["summaries"]
        pl_mappings = context["pl_mappings"]
        accounting_fee = context["accounting_fee"]
        interest_deductibility = context["interest_deductibility"]

        # Create lookups
        summary_by_category = {s.category_code: s for s in summaries}
        mapping_by_code = {m.category_code: m for m in pl_mappings}

        # Title
        ws["A1"] = "RENTAL PROPERTY PROFIT & LOSS"
        ws["A1"].font = Font(bold=True, size=16, color="1F4E79")
        ws.merge_cells("A1:E1")

        # Property details box
        ws["A3"] = "Client:"
        ws["B3"] = tax_return.client.name
        ws["A4"] = "Property:"
        ws["B4"] = tax_return.property_address
        ws["A5"] = "Tax Year:"
        ws["B5"] = tax_return.tax_year
        ws["D3"] = "Property Type:"
        ws["E3"] = "New Build" if tax_return.property_type.value == "new_build" else "Existing"
        ws["D4"] = "Year of Ownership:"
        ws["E4"] = tax_return.year_of_ownership
        ws["D5"] = "GST Registered:"
        ws["E5"] = "Yes" if tax_return.gst_registered else "No"

        for cell in ["A3", "A4", "A5", "D3", "D4", "D5"]:
            ws[cell].font = Font(bold=True)

        # Column headers
        row = 8
        headers = ["Row", "Category", "Amount", "Source", "Reference"]
        col_widths = [6, 35, 15, 10, 35]

        for col, (header, width) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.style = "header"
            ws.column_dimensions[get_column_letter(col)].width = width

        # INCOME Section
        row += 2
        ws.cell(row=row, column=1, value="")
        ws.cell(row=row, column=2, value="INCOME").style = "section"
        ws.merge_cells(f"B{row}:E{row}")

        income_categories = [
            ("rental_income", 6),
            ("water_rates_recovered", 7),
            ("bank_contribution", 8),
            ("insurance_payout", 9),
            ("other_income", 10),
        ]

        income_start_row = row + 1

        for cat_code, pl_row in income_categories:
            row += 1
            mapping = mapping_by_code.get(cat_code)
            summary = summary_by_category.get(cat_code)

            ws.cell(row=row, column=1, value=pl_row)
            ws.cell(row=row, column=2, value=mapping.display_name if mapping else cat_code)

            if summary and summary.gross_amount:
                amount = float(abs(summary.gross_amount))
                ref = self.sheet_references.get(cat_code)

                if ref:
                    ws.cell(row=row, column=3, value=f"={ref}").style = "income"
                    ws.cell(row=row, column=5, value=ref)
                else:
                    ws.cell(row=row, column=3, value=amount).style = "income"

                ws.cell(row=row, column=4, value=mapping.default_source if mapping else "BS")
            else:
                ws.cell(row=row, column=3, value=0).style = "currency"
                ws.cell(row=row, column=4, value="-")

        income_end_row = row

        # Total Income
        row += 1
        ws.cell(row=row, column=2, value="TOTAL INCOME").font = Font(bold=True)
        ws.cell(row=row, column=3, value=f"=SUM(C{income_start_row}:C{income_end_row})").style = "subtotal"
        total_income_row = row

        # EXPENSES Section
        row += 2
        ws.cell(row=row, column=2, value="EXPENSES").style = "section"
        ws.merge_cells(f"B{row}:E{row}")

        expense_categories = [
            ("agent_fees", 13),
            ("advertising", 14),
            ("bank_fees", 15),
            ("body_corporate", 16),
            ("consulting_accounting", 17),
            ("depreciation", 18),
            ("due_diligence", 19),
            ("electricity", 20),
            ("gas", 21),
            ("gardening", 22),
            ("healthy_homes", 23),
            ("hire_purchase", 24),
            ("insurance", 25),
            ("interest", 26),
            ("legal_fees", 27),
            ("listing_fees", 28),
            ("meth_testing", 29),
            ("mileage", 30),
            ("mortgage_admin", 31),
            ("pest_control", 32),
            ("postage_courier", 33),
            ("rates", 34),
            ("repairs_maintenance", 35),
            ("resident_society", 36),
            ("rubbish_collection", 37),
            ("security", 38),
            ("smoke_alarms", 39),
            ("subscriptions", 40),
            ("water_rates", 41),
        ]

        expense_start_row = row + 1

        for cat_code, pl_row in expense_categories:
            row += 1
            mapping = mapping_by_code.get(cat_code)
            summary = summary_by_category.get(cat_code)

            ws.cell(row=row, column=1, value=pl_row)
            ws.cell(row=row, column=2, value=mapping.display_name if mapping else cat_code)

            # Special handling
            if cat_code == "consulting_accounting":
                ws.cell(row=row, column=3, value=float(accounting_fee)).style = "expense"
                ws.cell(row=row, column=4, value="AF")
                ws.cell(row=row, column=5, value="Standard fee").style = "note"

            elif cat_code == "interest":
                ref = self.sheet_references.get("interest_deductible")
                if ref:
                    ws.cell(row=row, column=3, value=f"={ref}").style = "expense"
                    ws.cell(row=row, column=5, value=ref)
                elif summary:
                    ws.cell(row=row, column=3, value=float(abs(summary.deductible_amount or 0))).style = "expense"
                else:
                    ws.cell(row=row, column=3, value=0).style = "currency"

                ws.cell(row=row, column=4, value="IW")
                if interest_deductibility < 100:
                    note = f"{interest_deductibility}% deductible"
                else:
                    note = "100% deductible"
                ws.cell(row=row, column=5, value=note).style = "note"

            elif cat_code == "agent_fees":
                ref = self.sheet_references.get("agent_fees")
                if ref:
                    ws.cell(row=row, column=3, value=f"={ref}").style = "expense"
                    ws.cell(row=row, column=5, value=ref)
                elif summary:
                    ws.cell(row=row, column=3, value=float(abs(summary.deductible_amount or summary.gross_amount))).style = "expense"
                else:
                    ws.cell(row=row, column=3, value=0).style = "currency"
                ws.cell(row=row, column=4, value="PM")

            elif summary and (summary.deductible_amount or summary.gross_amount):
                amount = float(abs(summary.deductible_amount or summary.gross_amount))
                ref = self.sheet_references.get(cat_code)

                if ref:
                    ws.cell(row=row, column=3, value=f"={ref}").style = "expense"
                    ws.cell(row=row, column=5, value=ref)
                else:
                    ws.cell(row=row, column=3, value=amount).style = "expense"

                ws.cell(row=row, column=4, value=mapping.default_source if mapping else "BS")
            else:
                ws.cell(row=row, column=3, value=0).style = "currency"
                ws.cell(row=row, column=4, value="-")

        expense_end_row = row

        # Total Expenses
        row += 1
        ws.cell(row=row, column=2, value="TOTAL EXPENSES").font = Font(bold=True)
        ws.cell(row=row, column=3, value=f"=SUM(C{expense_start_row}:C{expense_end_row})").style = "subtotal"
        total_expense_row = row

        # Net Profit/Loss
        row += 2
        ws.cell(row=row, column=2, value="NET RENTAL INCOME / (LOSS)").font = Font(bold=True, size=12)
        ws.cell(row=row, column=3, value=f"=C{total_income_row}-C{total_expense_row}")
        ws.cell(row=row, column=3).style = "total"
        ws.cell(row=row, column=3).font = Font(bold=True, size=12)

        # Footer
        row += 3
        ws.cell(row=row, column=2, value=f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}").style = "note"
        row += 1
        ws.cell(row=row, column=2, value="Source Codes: BS=Bank Statement, PM=Property Manager, IW=Interest Workings, AF=Accounting Fee, SS=Settlement").style = "note"

    def _build_rental_bs_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the Rental Bank Statement sheet."""
        transactions = context["transactions"]
        pl_mappings = context["pl_mappings"]

        # Filter and sort
        bs_transactions = [t for t in transactions
                         if t.category_code and t.category_code not in ["unknown"]]
        bs_transactions.sort(key=lambda t: t.transaction_date)

        mapping_by_code = {m.category_code: m for m in pl_mappings}

        # Title
        ws["A1"] = "RENTAL BANK STATEMENT - CODED TRANSACTIONS"
        ws["A1"].font = Font(bold=True, size=14, color="1F4E79")
        ws.merge_cells("A1:G1")

        # Headers
        row = 3
        headers = ["Date", "Description", "Debit", "Credit", "Balance", "Category", "Code"]
        col_widths = [12, 45, 12, 12, 12, 25, 15]

        for col, (header, width) in enumerate(zip(headers, col_widths), 1):
            ws.cell(row=row, column=col, value=header).style = "header"
            ws.column_dimensions[get_column_letter(col)].width = width

        # Transactions
        start_row = row + 1
        for txn in bs_transactions:
            row += 1
            mapping = mapping_by_code.get(txn.category_code)

            ws.cell(row=row, column=1, value=txn.transaction_date).style = "date_style"
            ws.cell(row=row, column=2, value=txn.description[:45] if txn.description else "")

            if txn.amount < 0:
                ws.cell(row=row, column=3, value=float(abs(txn.amount))).style = "currency"
            else:
                ws.cell(row=row, column=4, value=float(txn.amount)).style = "currency"

            if txn.balance:
                ws.cell(row=row, column=5, value=float(txn.balance)).style = "currency"

            ws.cell(row=row, column=6, value=mapping.display_name if mapping else txn.category_code)
            ws.cell(row=row, column=7, value=txn.category_code)

        end_row = row

        # Summary section
        row += 3
        ws.cell(row=row, column=1, value="CATEGORY SUMMARY").style = "section"
        ws.merge_cells(f"A{row}:E{row}")

        row += 1
        for col, header in enumerate(["Category", "P&L Row", "Debit Total", "Credit Total", "Count"], 1):
            ws.cell(row=row, column=col, value=header).style = "subheader"

        # Group by category
        from collections import defaultdict
        category_totals = defaultdict(lambda: {"debit": Decimal("0"), "credit": Decimal("0"), "count": 0})

        for txn in bs_transactions:
            if txn.amount < 0:
                category_totals[txn.category_code]["debit"] += abs(txn.amount)
            else:
                category_totals[txn.category_code]["credit"] += txn.amount
            category_totals[txn.category_code]["count"] += 1

        summary_start_row = row + 1

        for cat_code in sorted(category_totals.keys()):
            row += 1
            data = category_totals[cat_code]
            mapping = mapping_by_code.get(cat_code)

            ws.cell(row=row, column=1, value=mapping.display_name if mapping else cat_code)
            ws.cell(row=row, column=2, value=mapping.pl_row if mapping else "")
            ws.cell(row=row, column=3, value=float(data["debit"])).style = "currency"
            ws.cell(row=row, column=4, value=float(data["credit"])).style = "currency"
            ws.cell(row=row, column=5, value=data["count"])

            # Store reference for P&L
            if mapping and mapping.pl_row:
                if mapping.transaction_type == "income":
                    self.sheet_references[cat_code] = f"'Rental BS'!D{row}"
                else:
                    self.sheet_references[cat_code] = f"'Rental BS'!C{row}"

    def _build_interest_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the Interest Workings sheet."""
        tax_return = context["tax_return"]
        transactions = context["transactions"]
        interest_deductibility = context["interest_deductibility"]

        interest_txns = [t for t in transactions if t.category_code == "interest"]
        interest_txns.sort(key=lambda t: t.transaction_date)

        # Title
        ws["A1"] = "INTEREST WORKINGS"
        ws["A1"].font = Font(bold=True, size=14, color="1F4E79")

        ws.column_dimensions["A"].width = 25
        ws.column_dimensions["B"].width = 15
        ws.column_dimensions["C"].width = 15
        ws.column_dimensions["D"].width = 35

        # Summary box
        ws["A3"] = "SUMMARY"
        ws["A3"].style = "section"
        ws.merge_cells("A3:D3")

        gross_interest = sum(abs(t.amount) for t in interest_txns)
        deductible_interest = gross_interest * Decimal(str(interest_deductibility / 100))
        capitalised_interest = gross_interest - deductible_interest

        ws["A4"] = "Gross Interest"
        ws["B4"] = float(gross_interest)
        ws["B4"].style = "currency"
        ws["C4"] = "=SUM(C:C)"  # Formula reference
        ws["D4"] = "Total from transaction detail below"
        ws["D4"].style = "note"

        ws["A5"] = "Deductible Percentage"
        ws["B5"] = interest_deductibility / 100
        ws["B5"].style = "percent"
        prop_type = "New Build" if tax_return.property_type.value == "new_build" else "Existing"
        ws["D5"] = f"{prop_type} - {tax_return.tax_year}"
        ws["D5"].style = "note"

        ws["A6"] = "Deductible Interest"
        ws["B6"] = f"=B4*B5"
        ws["B6"].style = "currency"
        ws["D6"] = "→ P&L Row 26"
        ws["D6"].font = Font(bold=True, color="006400")

        # Store reference for P&L
        self.sheet_references["interest_deductible"] = "'Interest Workings'!B6"

        ws["A7"] = "Capitalised Interest"
        ws["B7"] = f"=B4-B6"
        ws["B7"].style = "currency"
        ws["D7"] = "Non-deductible portion (add to cost base)"
        ws["D7"].style = "note"

        # Monthly breakdown
        row = 9
        ws.cell(row=row, column=1, value="MONTHLY BREAKDOWN").style = "section"
        ws.merge_cells(f"A{row}:D{row}")

        row += 1
        for col, header in enumerate(["Month", "# Charges", "Gross Amount", "Deductible"], 1):
            ws.cell(row=row, column=col, value=header).style = "subheader"

        # Group by month
        from collections import defaultdict
        monthly = defaultdict(lambda: {"amount": Decimal("0"), "count": 0})

        for txn in interest_txns:
            month_key = txn.transaction_date.strftime("%b-%y")
            monthly[month_key]["amount"] += abs(txn.amount)
            monthly[month_key]["count"] += 1

        def month_sort_key(month_str):
            return datetime.strptime(month_str, "%b-%y")

        monthly_start = row + 1
        for month_key in sorted(monthly.keys(), key=month_sort_key):
            row += 1
            data = monthly[month_key]

            ws.cell(row=row, column=1, value=month_key)
            ws.cell(row=row, column=2, value=data["count"])
            ws.cell(row=row, column=3, value=float(data["amount"])).style = "currency"
            ws.cell(row=row, column=4, value=f"=C{row}*$B$5").style = "currency"

        monthly_end = row

        # Totals
        row += 1
        ws.cell(row=row, column=1, value="TOTAL").font = Font(bold=True)
        ws.cell(row=row, column=2, value=f"=SUM(B{monthly_start}:B{monthly_end})")
        ws.cell(row=row, column=3, value=f"=SUM(C{monthly_start}:C{monthly_end})").style = "total"
        ws.cell(row=row, column=4, value=f"=SUM(D{monthly_start}:D{monthly_end})").style = "total"

        # Transaction detail
        row += 3
        ws.cell(row=row, column=1, value="TRANSACTION DETAIL").style = "section"
        ws.merge_cells(f"A{row}:D{row}")

        row += 1
        for col, header in enumerate(["Date", "Description", "Amount", "Running Total"], 1):
            ws.cell(row=row, column=col, value=header).style = "subheader"

        detail_start = row + 1
        running_total = Decimal("0")

        for txn in interest_txns:
            row += 1
            running_total += abs(txn.amount)

            ws.cell(row=row, column=1, value=txn.transaction_date).style = "date_style"
            ws.cell(row=row, column=2, value=txn.description[:35] if txn.description else "")
            ws.cell(row=row, column=3, value=float(abs(txn.amount))).style = "currency"
            ws.cell(row=row, column=4, value=float(running_total)).style = "currency"

        # Note
        row += 2
        if len(interest_txns) >= 20:
            ws.cell(row=row, column=1, value=f"Note: {len(interest_txns)} charges detected - likely bi-weekly interest").style = "note"
        else:
            ws.cell(row=row, column=1, value=f"Note: {len(interest_txns)} interest charges in period").style = "note"

    def _build_pm_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the Property Manager Statements sheet."""
        transactions = context["transactions"]
        pl_mappings = context["pl_mappings"]
        summaries = context["summaries"]

        mapping_by_code = {m.category_code: m for m in pl_mappings}

        # Title
        ws["A1"] = "PROPERTY MANAGER STATEMENTS"
        ws["A1"].font = Font(bold=True, size=14, color="1F4E79")
        ws.merge_cells("A1:E1")

        ws.column_dimensions["A"].width = 25
        ws.column_dimensions["B"].width = 15
        ws.column_dimensions["C"].width = 15
        ws.column_dimensions["D"].width = 12
        ws.column_dimensions["E"].width = 30

        # Summary
        ws["A3"] = "SUMMARY"
        ws["A3"].style = "section"
        ws.merge_cells("A3:E3")

        # Get PM-related categories
        pm_categories = ["rental_income", "agent_fees", "advertising", "listing_fees",
                        "repairs_maintenance", "gardening", "pest_control"]

        row = 4
        for col, header in enumerate(["Category", "P&L Row", "Amount", "Source"], 1):
            ws.cell(row=row, column=col, value=header).style = "subheader"

        summary_by_cat = {s.category_code: s for s in summaries}

        for cat_code in pm_categories:
            summary = summary_by_cat.get(cat_code)
            if summary and (summary.gross_amount or summary.deductible_amount):
                row += 1
                mapping = mapping_by_code.get(cat_code)

                ws.cell(row=row, column=1, value=mapping.display_name if mapping else cat_code)
                ws.cell(row=row, column=2, value=mapping.pl_row if mapping else "")

                amount = summary.deductible_amount or summary.gross_amount
                ws.cell(row=row, column=3, value=float(abs(amount))).style = "currency"
                ws.cell(row=row, column=4, value="PM/BS")

                # Store reference for agent fees
                if cat_code == "agent_fees":
                    self.sheet_references["agent_fees"] = f"'PM Statements'!C{row}"

        # PM Transaction Detail
        pm_txns = [t for t in transactions if t.category_code in pm_categories]
        pm_txns.sort(key=lambda t: t.transaction_date)

        if pm_txns:
            row += 3
            ws.cell(row=row, column=1, value="TRANSACTION DETAIL").style = "section"
            ws.merge_cells(f"A{row}:E{row}")

            row += 1
            for col, header in enumerate(["Date", "Description", "Amount", "Category"], 1):
                ws.cell(row=row, column=col, value=header).style = "subheader"

            for txn in pm_txns:
                row += 1
                mapping = mapping_by_code.get(txn.category_code)

                ws.cell(row=row, column=1, value=txn.transaction_date).style = "date_style"
                ws.cell(row=row, column=2, value=txn.description[:40] if txn.description else "")
                ws.cell(row=row, column=3, value=float(txn.amount)).style = "currency_neg"
                ws.cell(row=row, column=4, value=mapping.display_name if mapping else txn.category_code)

        # Notes
        row += 3
        ws.cell(row=row, column=1, value="Notes:").font = Font(bold=True)
        row += 1
        ws.cell(row=row, column=1, value="• Gross rent from PM statements should match bank deposits").style = "note"
        row += 1
        ws.cell(row=row, column=1, value="• Management fees typically 7-10% of gross rent").style = "note"
        row += 1
        ws.cell(row=row, column=1, value="• Verify repairs are deductible (not capital improvements)").style = "note"

    def _build_settlement_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the Settlement sheet for Year 1."""
        tax_return = context["tax_return"]
        transactions = context["transactions"]

        settlement_txns = [t for t in transactions
                         if hasattr(t, 'raw_data') and t.raw_data and t.raw_data.get("source") == "settlement"]

        # Title
        ws["A1"] = "SETTLEMENT STATEMENT WORKINGS"
        ws["A1"].font = Font(bold=True, size=14, color="1F4E79")

        ws["A2"] = f"Property: {tax_return.property_address}"
        ws["A3"] = "Year of Ownership: 1 (First Year)"

        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 15
        ws.column_dimensions["C"].width = 12
        ws.column_dimensions["D"].width = 35

        # Apportionments
        row = 5
        ws.cell(row=row, column=1, value="SETTLEMENT APPORTIONMENTS").style = "section"
        ws.merge_cells(f"A{row}:D{row}")

        row += 1
        for col, header in enumerate(["Item", "Amount", "P&L Row", "Notes"], 1):
            ws.cell(row=row, column=col, value=header).style = "subheader"

        # Expected items
        items = {
            "rates_apportionment": {"name": "Rates Apportionment", "pl_row": 34, "amount": Decimal("0")},
            "rates_vendor_credit": {"name": "Rates Vendor Credit", "pl_row": 34, "amount": Decimal("0"), "note": "Subtract from rates"},
            "body_corporate": {"name": "Body Corporate (Operating)", "pl_row": 16, "amount": Decimal("0")},
            "resident_society": {"name": "Resident Society", "pl_row": 36, "amount": Decimal("0")},
            "water_rates": {"name": "Water Rates", "pl_row": 41, "amount": Decimal("0")},
            "legal_fees": {"name": "Legal Fees", "pl_row": 27, "amount": Decimal("0")},
            "interest_on_deposit": {"name": "Interest on Deposit", "pl_row": 26, "amount": Decimal("0"), "note": "Credit against interest"},
        }

        for txn in settlement_txns:
            if hasattr(txn, 'raw_data') and txn.raw_data:
                txn_type = txn.raw_data.get("type", "")
                if txn_type in items:
                    items[txn_type]["amount"] = abs(txn.amount)

        for key, item in items.items():
            if item["amount"] > 0:
                row += 1
                ws.cell(row=row, column=1, value=item["name"])
                ws.cell(row=row, column=2, value=float(item["amount"])).style = "currency"
                ws.cell(row=row, column=3, value=item["pl_row"])
                if "note" in item:
                    ws.cell(row=row, column=4, value=item["note"]).style = "note"

        # Rates calculation
        row += 3
        ws.cell(row=row, column=1, value="RATES CALCULATION (Year 1)").style = "section"
        ws.merge_cells(f"A{row}:D{row}")

        rates_apport = items["rates_apportionment"]["amount"]
        vendor_credit = items["rates_vendor_credit"]["amount"]

        bs_rates = sum(abs(t.amount) for t in transactions
                      if t.category_code == "rates"
                      and (not hasattr(t, 'raw_data') or not t.raw_data or t.raw_data.get("source") != "settlement"))

        row += 1
        ws.cell(row=row, column=1, value="Settlement Apportionment")
        ws.cell(row=row, column=2, value=float(rates_apport)).style = "currency"

        row += 1
        ws.cell(row=row, column=1, value="+ Bank Statement Instalments")
        ws.cell(row=row, column=2, value=float(bs_rates)).style = "currency"

        row += 1
        ws.cell(row=row, column=1, value="- Vendor Credit")
        ws.cell(row=row, column=2, value=float(vendor_credit)).style = "currency"

        row += 1
        total_rates = rates_apport + bs_rates - vendor_credit
        ws.cell(row=row, column=1, value="= TOTAL RATES").font = Font(bold=True)
        ws.cell(row=row, column=2, value=float(total_rates)).style = "total"
        ws.cell(row=row, column=3, value="Row 34")

        # Store reference
        self.sheet_references["rates"] = f"'Settlement'!B{row}"

    def _build_depreciation_sheet(self, ws: Worksheet, context: Dict[str, Any]):
        """Build the Depreciation sheet."""
        tax_return = context["tax_return"]
        summaries = context["summaries"]

        dep_summary = next((s for s in summaries if s.category_code == "depreciation"), None)

        # Title
        ws["A1"] = "DEPRECIATION WORKINGS"
        ws["A1"].font = Font(bold=True, size=14, color="1F4E79")

        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 15
        ws.column_dimensions["C"].width = 15
        ws.column_dimensions["D"].width = 30

        # Property info
        ws["A3"] = "Property:"
        ws["B3"] = tax_return.property_address
        ws["A4"] = "Tax Year:"
        ws["B4"] = tax_return.tax_year
        ws["A5"] = "Year of Ownership:"
        ws["B5"] = tax_return.year_of_ownership

        for cell in ["A3", "A4", "A5"]:
            ws[cell].font = Font(bold=True)

        # Depreciation summary
        row = 7
        ws.cell(row=row, column=1, value="CHATTELS DEPRECIATION").style = "section"
        ws.merge_cells(f"A{row}:D{row}")

        if dep_summary:
            row += 2
            ws.cell(row=row, column=1, value="Full Year Depreciation")
            ws.cell(row=row, column=2, value=float(abs(dep_summary.gross_amount))).style = "currency"
            ws.cell(row=row, column=3, value="From Chattel Pack")

            if tax_return.year_of_ownership == 1:
                row += 2
                ws.cell(row=row, column=1, value="PRO-RATA CALCULATION").style = "section"
                ws.merge_cells(f"A{row}:D{row}")

                row += 1
                ws.cell(row=row, column=1, value="Formula:")
                ws.cell(row=row, column=2, value="Full Year × (Months ÷ 12)")

                row += 1
                ws.cell(row=row, column=1, value="Months Owned:")
                ws.cell(row=row, column=2, value="[Enter months from settlement to 31 March]")

                row += 1
                ws.cell(row=row, column=1, value="Pro-rated Amount:")
                ws.cell(row=row, column=2, value="=B9*(B12/12)").style = "currency"

            row += 2
            ws.cell(row=row, column=1, value="Deductible Depreciation").font = Font(bold=True)
            ws.cell(row=row, column=2, value=float(abs(dep_summary.deductible_amount or dep_summary.gross_amount))).style = "total"
            ws.cell(row=row, column=3, value="→ P&L Row 18").font = Font(bold=True, color="006400")

            # Store reference
            self.sheet_references["depreciation"] = f"'Depreciation'!B{row}"
        else:
            row += 2
            ws.cell(row=row, column=1, value="No depreciation data found").style = "note"
            ws.cell(row=row, column=2, value="Upload Chattel Pack to calculate")

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
        """Load transaction summaries."""
        result = await db.execute(
            select(TransactionSummary)
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