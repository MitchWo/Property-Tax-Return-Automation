#!/usr/bin/env python3
"""
Verify Property Tax Agent setup and dependencies.

This script checks that all required components are properly installed
and configured before running tests or the application.
"""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import psutil
from rich.console import Console
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Initialize Rich console for pretty output
console = Console()


class SetupVerifier:
    """Verify system setup and configuration."""

    def __init__(self):
        self.checks_passed = []
        self.checks_failed = []
        self.warnings = []

    def check_python_version(self) -> bool:
        """Check Python version is 3.11+."""
        version = sys.version_info
        if version.major == 3 and version.minor >= 11:
            self.checks_passed.append(f"Python {version.major}.{version.minor}.{version.micro}")
            return True
        else:
            self.checks_failed.append(
                f"Python version {version.major}.{version.minor} (requires 3.11+)"
            )
            return False

    def check_required_packages(self) -> bool:
        """Check required packages are installed."""
        required_packages = [
            "fastapi",
            "sqlalchemy",
            "alembic",
            "pydantic",
            "anthropic",
            "openpyxl",
            "pytest",
            "pytest-asyncio",
            "python-multipart",
            "pdfplumber",
            "aiofiles",
            "httpx",
        ]

        all_installed = True
        for package in required_packages:
            try:
                importlib.import_module(package.replace("-", "_"))
                self.checks_passed.append(f"Package: {package}")
            except ImportError:
                self.checks_failed.append(f"Package not installed: {package}")
                all_installed = False

        return all_installed

    def check_env_variables(self) -> bool:
        """Check required environment variables."""
        required_vars = ["ANTHROPIC_API_KEY", "DATABASE_URL"]
        optional_vars = ["OPENAI_API_KEY", "REDIS_URL", "AWS_ACCESS_KEY_ID"]

        all_required = True
        for var in required_vars:
            if os.getenv(var):
                self.checks_passed.append(f"Environment: {var}")
            else:
                self.checks_failed.append(f"Missing environment variable: {var}")
                all_required = False

        for var in optional_vars:
            if not os.getenv(var):
                self.warnings.append(f"Optional environment variable not set: {var}")

        return all_required

    def check_directory_structure(self) -> bool:
        """Check required directories exist."""
        required_dirs = [
            "app",
            "app/api",
            "app/core",
            "app/models",
            "app/schemas",
            "app/services",
            "app/templates",
            "app/rules",
            "app/skills",
            "tests",
            "uploads",
            "processed",
        ]

        all_exist = True
        for dir_path in required_dirs:
            path = Path(dir_path)
            if path.exists():
                self.checks_passed.append(f"Directory: {dir_path}")
            else:
                # Try to create missing directories
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    self.warnings.append(f"Created missing directory: {dir_path}")
                except Exception as e:
                    self.checks_failed.append(f"Missing directory: {dir_path} ({e})")
                    all_exist = False

        return all_exist

    def check_config_files(self) -> bool:
        """Check required configuration files."""
        config_files = [
            ("pyproject.toml", "Project configuration"),
            (".env", "Environment configuration"),
            ("alembic.ini", "Database migration configuration"),
            ("app/rules/categorization.yaml", "Categorization rules"),
            ("app/rules/bank_parsers.yaml", "Bank parser configurations"),
        ]

        all_exist = True
        for file_path, description in config_files:
            path = Path(file_path)
            if path.exists():
                self.checks_passed.append(f"Config: {description}")
            else:
                if file_path == ".env":
                    self.warnings.append(f"Missing {description} - create from .env.example")
                else:
                    self.checks_failed.append(f"Missing config file: {description}")
                    all_exist = False

        return all_exist

    async def check_database_connection(self) -> bool:
        """Check database connection and tables."""
        try:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                self.checks_failed.append("DATABASE_URL not set")
                return False

            engine = create_async_engine(database_url)
            async with engine.begin() as conn:
                # Check connection
                result = await conn.execute(text("SELECT 1"))
                result.scalar()
                self.checks_passed.append("Database connection")

                # Check for required tables
                tables_query = """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
                """
                result = await conn.execute(text(tables_query))
                tables = [row[0] for row in result]

                required_tables = [
                    "alembic_version",
                    "documents",
                    "transactions",
                    "tax_rules",
                    "pl_row_mappings",
                ]

                for table in required_tables:
                    if table in tables:
                        self.checks_passed.append(f"Table: {table}")
                    else:
                        self.checks_failed.append(f"Missing table: {table}")

            await engine.dispose()
            return True

        except Exception as e:
            self.checks_failed.append(f"Database error: {str(e)}")
            return False

    async def check_seed_data(self) -> bool:
        """Check if seed data is loaded."""
        try:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                return False

            engine = create_async_engine(database_url)
            async with engine.begin() as conn:
                # Check tax rules
                result = await conn.execute(text("SELECT COUNT(*) FROM tax_rules"))
                tax_rules_count = result.scalar()

                # Check P&L mappings
                result = await conn.execute(text("SELECT COUNT(*) FROM pl_row_mappings"))
                pl_mappings_count = result.scalar()

                if tax_rules_count > 0:
                    self.checks_passed.append(f"Tax rules loaded: {tax_rules_count}")
                else:
                    self.warnings.append("No tax rules loaded - run seed_data.py")

                if pl_mappings_count > 0:
                    self.checks_passed.append(f"P&L mappings loaded: {pl_mappings_count}")
                else:
                    self.warnings.append("No P&L mappings loaded - run seed_data.py")

            await engine.dispose()
            return True

        except Exception as e:
            self.warnings.append(f"Could not check seed data: {str(e)}")
            return False

    def check_disk_space(self) -> bool:
        """Check available disk space."""
        usage = psutil.disk_usage("/")
        free_gb = usage.free / (1024**3)

        if free_gb > 1:
            self.checks_passed.append(f"Disk space: {free_gb:.1f} GB free")
            return True
        else:
            self.warnings.append(f"Low disk space: {free_gb:.1f} GB free")
            return free_gb > 0.1

    def check_memory(self) -> bool:
        """Check available memory."""
        memory = psutil.virtual_memory()
        available_gb = memory.available / (1024**3)

        if available_gb > 1:
            self.checks_passed.append(f"Memory: {available_gb:.1f} GB available")
            return True
        else:
            self.warnings.append(f"Low memory: {available_gb:.1f} GB available")
            return available_gb > 0.5

    def generate_report(self) -> None:
        """Generate verification report."""
        console.print("\n[bold blue]Setup Verification Report[/bold blue]\n")

        # Summary
        total_checks = len(self.checks_passed) + len(self.checks_failed)
        success_rate = (
            (len(self.checks_passed) / total_checks * 100) if total_checks > 0 else 0
        )

        if len(self.checks_failed) == 0:
            console.print("[bold green]✅ All checks passed![/bold green]")
        else:
            console.print(f"[yellow]⚠️  {len(self.checks_failed)} checks failed[/yellow]")

        console.print(f"Success rate: {success_rate:.1f}%\n")

        # Passed checks
        if self.checks_passed:
            table = Table(title="Passed Checks", style="green")
            table.add_column("Component", style="cyan")
            for check in self.checks_passed:
                table.add_row(f"✓ {check}")
            console.print(table)

        # Failed checks
        if self.checks_failed:
            table = Table(title="Failed Checks", style="red")
            table.add_column("Component", style="red")
            for check in self.checks_failed:
                table.add_row(f"✗ {check}")
            console.print(table)

        # Warnings
        if self.warnings:
            table = Table(title="Warnings", style="yellow")
            table.add_column("Warning", style="yellow")
            for warning in self.warnings:
                table.add_row(f"⚠ {warning}")
            console.print(table)

    def save_report(self, path: Path) -> None:
        """Save verification report to file."""
        report = {
            "timestamp": str(Path.cwd()),
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "warnings": self.warnings,
            "success_rate": (
                len(self.checks_passed)
                / (len(self.checks_passed) + len(self.checks_failed))
                * 100
                if (len(self.checks_passed) + len(self.checks_failed)) > 0
                else 0
            ),
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2))
        console.print(f"\n[dim]Report saved to {path}[/dim]")


async def main():
    """Run setup verification."""
    verifier = SetupVerifier()

    console.print("[bold]Running setup verification...[/bold]\n")

    # Run checks
    checks = [
        ("Python version", verifier.check_python_version()),
        ("Required packages", verifier.check_required_packages()),
        ("Environment variables", verifier.check_env_variables()),
        ("Directory structure", verifier.check_directory_structure()),
        ("Configuration files", verifier.check_config_files()),
        ("Database connection", await verifier.check_database_connection()),
        ("Seed data", await verifier.check_seed_data()),
        ("Disk space", verifier.check_disk_space()),
        ("Memory", verifier.check_memory()),
    ]

    # Generate report
    verifier.generate_report()

    # Save report
    report_path = Path("reports") / "setup_verification.json"
    verifier.save_report(report_path)

    # Exit code
    if len(verifier.checks_failed) > 0:
        console.print("\n[red]❌ Setup verification failed. Please fix the issues above.[/red]")
        sys.exit(1)
    else:
        console.print("\n[green]✅ Setup verification complete![/green]")

        if verifier.warnings:
            console.print("[yellow]Note: Some warnings were found but are not critical.[/yellow]")

        # Provide next steps
        console.print("\n[bold]Next steps:[/bold]")
        console.print("1. Run database migrations: [cyan]poetry run alembic upgrade head[/cyan]")
        console.print("2. Load seed data: [cyan]poetry run python app/services/seed_data.py[/cyan]")
        console.print("3. Run tests: [cyan]poetry run pytest[/cyan]")
        console.print("4. Start server: [cyan]poetry run uvicorn app.main:app --reload[/cyan]")


if __name__ == "__main__":
    asyncio.run(main())