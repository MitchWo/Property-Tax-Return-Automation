#!/usr/bin/env python3
"""Test script for pattern matching functionality."""

import sys
from pathlib import Path

# Add the app directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.rules.loader import get_pattern_matcher, get_bank_parser


def test_pattern_matching():
    """Test the pattern matcher with various transaction examples."""
    print("=" * 80)
    print("TESTING PATTERN MATCHING")
    print("=" * 80)

    matcher = get_pattern_matcher()

    # Test transactions
    test_cases = [
        # Exact payee matches
        {
            "description": "Council rates payment",
            "other_party": "AUCKLAND COUNCIL",
            "amount": -800.00,
            "type": "expense"
        },
        {
            "description": "Water charges",
            "other_party": "WATERCARE",
            "amount": -150.00,
            "type": "expense"
        },
        {
            "description": "Property management",
            "other_party": "QUINOVIC",
            "amount": -200.00,
            "type": "expense"
        },

        # Pattern matches
        {
            "description": "Mortgage interest charged",
            "other_party": "ANZ Bank",
            "amount": -1500.00,
            "type": "expense"
        },
        {
            "description": "Body corporate levy payment",
            "other_party": None,
            "amount": -400.00,
            "type": "expense"
        },
        {
            "description": "Plumber repair leaking tap",
            "other_party": "Joe's Plumbing",
            "amount": -250.00,
            "type": "expense"
        },

        # Income transactions
        {
            "description": "Rent payment received from tenant",
            "other_party": "John Smith",
            "amount": 650.00,
            "type": "income"
        },
        {
            "description": "Bond payment from tenant",
            "other_party": "Jane Doe",
            "amount": 2600.00,
            "type": "income"
        },

        # Keywords
        {
            "description": "Insurance renewal",
            "other_party": None,
            "amount": -1200.00,
            "type": "expense"
        },
        {
            "description": "Garden maintenance",
            "other_party": "Green Thumbs Ltd",
            "amount": -120.00,
            "type": "expense"
        },

        # Transfers (should be excluded)
        {
            "description": "Transfer to savings account",
            "other_party": None,
            "amount": -5000.00,
            "type": "expense"
        },

        # Edge cases
        {
            "description": "Purchase at Bunnings Warehouse",
            "other_party": "BUNNINGS",
            "amount": -85.50,
            "type": "expense"
        },
        {
            "description": "Credit interest on offset account",
            "other_party": "Kiwibank",
            "amount": 25.00,
            "type": "income"
        },
    ]

    print("\nTesting transaction categorization:\n")

    for i, test in enumerate(test_cases, 1):
        print(f"Test {i}:")
        print(f"  Description: {test['description']}")
        print(f"  Other Party: {test.get('other_party', 'N/A')}")
        print(f"  Amount: ${test['amount']:.2f}")
        print(f"  Type: {test['type']}")

        result = matcher.match(
            description=test["description"],
            other_party=test.get("other_party"),
            amount=test["amount"],
            transaction_type=test["type"]
        )

        if result:
            print(f"  ✓ MATCHED:")
            print(f"    Category: {result['category']}")
            print(f"    Confidence: {result['confidence']:.0%}")
            print(f"    Source: {result['source']}")
            if result.get('flag_for_review'):
                print(f"    ⚠️  FLAGGED: {result.get('review_reason', 'Review required')}")
            if result.get('notes'):
                print(f"    Notes: {result['notes']}")
        else:
            print(f"  ✗ NO MATCH")

        print()


def test_bank_parsers():
    """Test bank parser configuration loading."""
    print("=" * 80)
    print("TESTING BANK PARSERS")
    print("=" * 80)

    test_banks = [
        "ASB",
        "ANZ Bank",
        "Kiwibank",
        "Westpac",
        "Unknown Bank"
    ]

    print("\nTesting bank parser lookups:\n")

    for bank in test_banks:
        parser = get_bank_parser(bank)
        if parser:
            print(f"✓ {bank}:")
            print(f"  Name: {parser.get('name', 'N/A')}")
            print(f"  Date format: {parser['csv_format']['date_format']}")
            print(f"  Amount style: {parser['amount_style']}")
            print(f"  Has header: {parser['csv_format']['has_header']}")
        else:
            print(f"✗ {bank}: No parser found")
        print()


def test_amount_rules():
    """Test amount-based flagging rules."""
    print("=" * 80)
    print("TESTING AMOUNT RULES")
    print("=" * 80)

    matcher = get_pattern_matcher()

    # Test unusual amounts
    test_cases = [
        {
            "description": "Rent payment",
            "other_party": None,
            "amount": 5000.00,  # Unusually high rent
            "type": "income"
        },
        {
            "description": "Council rates",
            "other_party": "WELLINGTON CITY COUNCIL",
            "amount": -50.00,  # Unusually low rates
            "type": "expense"
        },
        {
            "description": "Insurance premium",
            "other_party": "TOWER INSURANCE",
            "amount": -5000.00,  # High insurance
            "type": "expense"
        },
    ]

    print("\nTesting amount-based flagging:\n")

    for test in test_cases:
        print(f"Transaction: {test['description']}")
        print(f"  Amount: ${abs(test['amount']):.2f}")

        result = matcher.match(
            description=test["description"],
            other_party=test.get("other_party"),
            amount=test["amount"],
            transaction_type=test["type"]
        )

        if result and result.get('flag_for_review'):
            print(f"  ⚠️  FLAGGED: {result.get('review_reason')}")
        else:
            print(f"  ✓ Amount within typical range")
        print()


if __name__ == "__main__":
    test_pattern_matching()
    test_bank_parsers()
    test_amount_rules()

    print("=" * 80)
    print("TESTING COMPLETE")
    print("=" * 80)