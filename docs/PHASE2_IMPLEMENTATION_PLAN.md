# Phase 2 (AI Brain) Implementation Plan

## Executive Summary

This plan addresses the core consistency problem: "sometimes it identifies bank contributions, sometimes it doesn't; sometimes it applies a certain logic to PM statements, but when you re-process it, it uses another."

**Root Cause**: The current single-call approach allows implicit decision-making by AI, leading to inconsistent results.

**Solution**: Hybrid architecture combining:
1. **Romulus 5-Phase Structure** - Clear scope boundaries prevent scope creep
2. **Structured Checkpoints** - Explicit IDENTIFY → CLASSIFY → VALIDATE → CALCULATE within each phase
3. **Tool Use Schemas** - Guaranteed output structure enforcement
4. **Multi-Pass Verification** - AI audits itself before finalizing

---

## Current State Analysis

### Current Architecture (brain.py)
```
Single AI Call → Parse JSON → QA Validation → Save
```

### Problems Identified
| Issue | Impact |
|-------|--------|
| Single monolithic prompt (~1100 lines) | AI loses focus, makes implicit decisions |
| Free-form JSON output | Parsing failures, missing fields |
| No explicit reasoning checkpoints | Cannot trace WHY a decision was made |
| QA runs AFTER generation | Too late to enforce consistency |
| No cross-phase validation | Bank contribution found in one run, missed in next |

---

## Proposed Architecture

### High-Level Flow
```
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 2: AI BRAIN v2.0                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Phase 2A   │  │  Phase 2B   │  │  Phase 2C   │             │
│  │  Interest   │→ │   Expense   │→ │     P&L     │             │
│  │ Calculation │  │   Coding    │  │  Completion │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│        ↓                ↓                ↓                      │
│  ┌─────────────────────────────────────────────────┐           │
│  │              Phase 2D: QA Review                 │           │
│  │         (Cross-validates all phases)             │           │
│  └─────────────────────────────────────────────────┘           │
│        ↓                                                        │
│  ┌─────────────────────────────────────────────────┐           │
│  │           Phase 2E: Reconciliation               │           │
│  │    (Resolves discrepancies, finalizes output)    │           │
│  └─────────────────────────────────────────────────┘           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Each Phase Has Tool Use Schema** - Output structure is GUARANTEED
2. **Checkpoints Within Phases** - Explicit reasoning at each step
3. **Phase Boundaries Are Strict** - No scope creep between phases
4. **Cross-Phase Verification** - QA validates consistency across phases
5. **Audit Trail** - Every decision is traceable

---

## Implementation Phases

### Phase 2A: Interest Calculation

**Scope Boundaries:**
- ✅ Find ALL interest transactions
- ✅ Calculate gross interest total
- ✅ Apply deductibility percentage
- ✅ Handle offset accounts
- ✅ Handle Year 1 interest on deposit
- ❌ Do NOT code other expenses
- ❌ Do NOT calculate P&L totals

**Tool Use Schema:**
```python
INTEREST_CALCULATION_SCHEMA = {
    "name": "calculate_interest",
    "description": "Calculate interest expense for NZ rental property tax return",
    "input_schema": {
        "type": "object",
        "required": [
            "checkpoint_identify",
            "checkpoint_classify",
            "checkpoint_validate",
            "checkpoint_calculate",
            "interest_result"
        ],
        "properties": {
            "checkpoint_identify": {
                "type": "object",
                "description": "STEP 1: Identify ALL potential interest transactions",
                "required": ["loan_accounts_found", "interest_transactions", "data_source_used"],
                "properties": {
                    "loan_accounts_found": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of loan account numbers/names identified"
                    },
                    "interest_transactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["date", "description", "amount", "source", "is_debit"],
                            "properties": {
                                "date": {"type": "string"},
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "source": {"type": "string"},
                                "is_debit": {"type": "boolean"},
                                "loan_account": {"type": "string"}
                            }
                        }
                    },
                    "data_source_used": {
                        "type": "string",
                        "enum": ["csv_loan_statement", "pdf_loan_statement", "bank_statement"],
                        "description": "Primary data source (prefer CSV over PDF)"
                    },
                    "transaction_count": {"type": "integer"},
                    "expected_frequency": {
                        "type": "string",
                        "enum": ["weekly", "bi-weekly", "monthly", "irregular"]
                    }
                }
            },
            "checkpoint_classify": {
                "type": "object",
                "description": "STEP 2: Classify each transaction as INCLUDE or EXCLUDE",
                "required": ["included_transactions", "excluded_transactions"],
                "properties": {
                    "included_transactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["description", "amount", "reason_included"],
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "reason_included": {"type": "string"}
                            }
                        }
                    },
                    "excluded_transactions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["description", "amount", "reason_excluded"],
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "reason_excluded": {
                                    "type": "string",
                                    "enum": [
                                        "credit_not_debit",
                                        "interest_adjustment",
                                        "offset_benefit",
                                        "savings_interest",
                                        "capitalised_interest",
                                        "not_interest_related"
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            "checkpoint_validate": {
                "type": "object",
                "description": "STEP 3: Validate the classification",
                "required": ["frequency_check", "offset_check", "total_reasonableness"],
                "properties": {
                    "frequency_check": {
                        "type": "object",
                        "required": ["expected_count", "actual_count", "is_valid", "explanation"],
                        "properties": {
                            "expected_count": {"type": "integer"},
                            "actual_count": {"type": "integer"},
                            "is_valid": {"type": "boolean"},
                            "explanation": {"type": "string"}
                        }
                    },
                    "offset_check": {
                        "type": "object",
                        "required": ["has_offset_account", "offset_indicator"],
                        "properties": {
                            "has_offset_account": {"type": "boolean"},
                            "offset_indicator": {"type": "string"}
                        }
                    },
                    "total_reasonableness": {
                        "type": "object",
                        "required": ["gross_total", "is_reasonable", "explanation"],
                        "properties": {
                            "gross_total": {"type": "number"},
                            "is_reasonable": {"type": "boolean"},
                            "explanation": {"type": "string"}
                        }
                    },
                    "cross_validation": {
                        "type": "object",
                        "properties": {
                            "compared_to": {"type": "string"},
                            "variance": {"type": "number"},
                            "variance_explanation": {"type": "string"}
                        }
                    }
                }
            },
            "checkpoint_calculate": {
                "type": "object",
                "description": "STEP 4: Calculate final amounts",
                "required": ["gross_interest", "deductibility_percentage", "deductible_interest", "calculation_breakdown"],
                "properties": {
                    "gross_interest": {"type": "number"},
                    "interest_on_deposit": {"type": "number", "description": "Year 1 only - net against expense"},
                    "deductibility_percentage": {"type": "number"},
                    "deductible_interest": {"type": "number"},
                    "calculation_breakdown": {
                        "type": "object",
                        "properties": {
                            "formula": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    },
                    "monthly_breakdown": {
                        "type": "object",
                        "additionalProperties": {"type": "number"}
                    }
                }
            },
            "interest_result": {
                "type": "object",
                "required": ["gross_amount", "deductible_amount", "pl_row", "source_code", "verification_status"],
                "properties": {
                    "gross_amount": {"type": "number"},
                    "deductible_amount": {"type": "number"},
                    "pl_row": {"type": "integer", "const": 25},
                    "source_code": {"type": "string"},
                    "verification_status": {
                        "type": "string",
                        "enum": ["verified", "needs_review", "estimated"]
                    },
                    "notes": {"type": "string"},
                    "flags": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["severity", "message"],
                            "properties": {
                                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                                "message": {"type": "string"},
                                "action_required": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
}
```

**Additional Checks (from NZ Tax Research):**

| Check | Implementation |
|-------|----------------|
| Interest rate year verification | Validate: FY24=50%, FY25=80%, FY26+=100% for existing |
| New build CCC validation | IF 100% claimed, REQUIRE CCC document with date >= 27/03/2020 |
| Offset account netting | Detect "offset" in descriptions, validate interest rate on net balance |
| Interest on deposit (Year 1) | Extract from settlement statement, NET against expense |
| Bi-weekly pattern validation | Count ~24-26 transactions, validate dates ~14 days apart |
| Multiple loan aggregation | Group by property address, sum all loan interest |

---

### Phase 2B: Expense Coding

**Scope Boundaries:**
- ✅ Code ALL non-interest transactions
- ✅ Apply category rules (BC operating vs reserve, etc.)
- ✅ Handle Year 1 settlement items
- ✅ Flag repairs >$800 needing invoices
- ❌ Do NOT recalculate interest (use Phase 2A output)
- ❌ Do NOT calculate final P&L totals

**Tool Use Schema:**
```python
EXPENSE_CODING_SCHEMA = {
    "name": "code_expenses",
    "description": "Categorize all expenses for NZ rental property tax return",
    "input_schema": {
        "type": "object",
        "required": [
            "checkpoint_identify",
            "checkpoint_classify",
            "checkpoint_validate",
            "checkpoint_calculate",
            "expense_results"
        ],
        "properties": {
            "checkpoint_identify": {
                "type": "object",
                "description": "STEP 1: Identify ALL expense transactions",
                "required": ["all_debits", "excluded_items"],
                "properties": {
                    "all_debits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["date", "description", "amount", "source"],
                            "properties": {
                                "date": {"type": "string"},
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "source": {"type": "string"},
                                "other_party": {"type": "string"}
                            }
                        }
                    },
                    "excluded_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["description", "amount", "exclusion_reason"],
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "exclusion_reason": {
                                    "type": "string",
                                    "enum": [
                                        "principal_repayment",
                                        "transfer_to_personal",
                                        "drawing",
                                        "personal_insurance",
                                        "bc_reserve_fund",
                                        "capital_expense",
                                        "not_property_related"
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            "checkpoint_classify": {
                "type": "object",
                "description": "STEP 2: Classify each expense into P&L category",
                "required": ["categorized_expenses"],
                "properties": {
                    "categorized_expenses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["description", "amount", "category_code", "pl_row", "classification_reason"],
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "number"},
                                "category_code": {
                                    "type": "string",
                                    "enum": [
                                        "rates", "water_rates", "body_corporate", "resident_society",
                                        "insurance", "agent_fees", "advertising", "bank_fees",
                                        "legal_fees", "repairs_maintenance", "due_diligence",
                                        "depreciation", "other_expenses"
                                    ]
                                },
                                "pl_row": {"type": "integer"},
                                "classification_reason": {"type": "string"},
                                "requires_invoice": {"type": "boolean"},
                                "invoice_status": {
                                    "type": "string",
                                    "enum": ["verified", "missing_required", "not_required"]
                                }
                            }
                        }
                    }
                }
            },
            "checkpoint_validate": {
                "type": "object",
                "description": "STEP 3: Validate expense classifications",
                "required": ["validation_checks"],
                "properties": {
                    "validation_checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["check_name", "passed", "details"],
                            "properties": {
                                "check_name": {
                                    "type": "string",
                                    "enum": [
                                        "repairs_vs_capital",
                                        "bc_operating_vs_reserve",
                                        "insurance_type",
                                        "legal_fees_deductibility",
                                        "pm_fee_reasonableness",
                                        "year1_rates_apportionment",
                                        "ring_fencing_check"
                                    ]
                                },
                                "passed": {"type": "boolean"},
                                "details": {"type": "string"},
                                "amount_affected": {"type": "number"}
                            }
                        }
                    },
                    "year1_settlement_checks": {
                        "type": "object",
                        "properties": {
                            "rates_apportionment_included": {"type": "boolean"},
                            "bc_prorata_included": {"type": "boolean"},
                            "rs_prorata_included": {"type": "boolean"},
                            "legal_fees_captured": {"type": "boolean"}
                        }
                    }
                }
            },
            "checkpoint_calculate": {
                "type": "object",
                "description": "STEP 4: Calculate category totals",
                "required": ["category_totals"],
                "properties": {
                    "category_totals": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "required": ["gross_amount", "deductible_amount", "transaction_count"],
                            "properties": {
                                "gross_amount": {"type": "number"},
                                "deductible_amount": {"type": "number"},
                                "transaction_count": {"type": "integer"},
                                "calculation_steps": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            },
            "expense_results": {
                "type": "object",
                "description": "Final categorized expenses",
                "additionalProperties": {
                    "type": "object",
                    "required": ["pl_row", "gross_amount", "deductible_amount", "source_code"],
                    "properties": {
                        "pl_row": {"type": "integer"},
                        "gross_amount": {"type": "number"},
                        "deductible_amount": {"type": "number"},
                        "deductible_percentage": {"type": "number"},
                        "source_code": {"type": "string"},
                        "source": {"type": "string"},
                        "verification_status": {"type": "string"},
                        "notes": {"type": "string"},
                        "transactions": {"type": "array"}
                    }
                }
            }
        }
    }
}
```

**Additional Checks (from NZ Tax Research):**

| Check | Implementation |
|-------|----------------|
| Repairs vs Capital | Flag amounts >$5,000, require justification for "repair" classification |
| Body Corporate Split | REQUIRE invoice check for operating vs reserve |
| Insurance Type | Validate "landlord" or "rental property" in description |
| Rates Apportionment (Year 1) | Calculate: (Days owned / 365) × Annual rates |
| PM Fee Reasonableness | Validate ratio 7-12% of rent collected |
| Legal Fees Classification | <$10k purchase legal = deductible; dispute/recovery = deductible |
| Healthy Homes Treatment | First install = capital, service = revenue |
| Ring-Fencing Check | IF loss, flag for ring-fencing compliance |

---

### Phase 2C: P&L Completion

**Scope Boundaries:**
- ✅ Assemble income section
- ✅ Combine Phase 2A (interest) + Phase 2B (expenses)
- ✅ Calculate totals
- ✅ Add standard accounting fee
- ❌ Do NOT re-categorize expenses
- ❌ Do NOT recalculate interest

**Tool Use Schema:**
```python
PL_COMPLETION_SCHEMA = {
    "name": "complete_pl",
    "description": "Complete P&L assembly for NZ rental property tax return",
    "input_schema": {
        "type": "object",
        "required": [
            "checkpoint_identify",
            "checkpoint_validate",
            "checkpoint_calculate",
            "pl_result"
        ],
        "properties": {
            "checkpoint_identify": {
                "type": "object",
                "description": "STEP 1: Identify ALL income sources",
                "required": ["income_sources"],
                "properties": {
                    "income_sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["source", "category", "amount", "pl_row"],
                            "properties": {
                                "source": {"type": "string"},
                                "category": {
                                    "type": "string",
                                    "enum": [
                                        "rental_income",
                                        "water_rates_recovered",
                                        "bank_contribution",
                                        "insurance_payout",
                                        "other_income"
                                    ]
                                },
                                "amount": {"type": "number"},
                                "pl_row": {"type": "integer"}
                            }
                        }
                    },
                    "bank_contribution_check": {
                        "type": "object",
                        "required": ["searched_for", "found", "evidence"],
                        "properties": {
                            "searched_for": {"type": "boolean", "const": true},
                            "found": {"type": "boolean"},
                            "evidence": {"type": "string"},
                            "amount": {"type": "number"}
                        }
                    }
                }
            },
            "checkpoint_validate": {
                "type": "object",
                "description": "STEP 2: Validate all figures",
                "required": ["reconciliation_checks"],
                "properties": {
                    "reconciliation_checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["check_name", "passed", "details"],
                            "properties": {
                                "check_name": {
                                    "type": "string",
                                    "enum": [
                                        "income_total_crossfoot",
                                        "expense_total_crossfoot",
                                        "source_tracing_complete",
                                        "bank_reconciliation",
                                        "pm_reconciliation"
                                    ]
                                },
                                "passed": {"type": "boolean"},
                                "expected": {"type": "number"},
                                "actual": {"type": "number"},
                                "variance": {"type": "number"},
                                "details": {"type": "string"}
                            }
                        }
                    },
                    "gst_consistency_check": {
                        "type": "object",
                        "required": ["gst_registered", "treatment_consistent"],
                        "properties": {
                            "gst_registered": {"type": "boolean"},
                            "treatment_consistent": {"type": "boolean"},
                            "notes": {"type": "string"}
                        }
                    }
                }
            },
            "checkpoint_calculate": {
                "type": "object",
                "description": "STEP 3: Calculate final totals",
                "required": ["income_total", "expense_total", "net_rental_income"],
                "properties": {
                    "income_total": {
                        "type": "object",
                        "required": ["amount", "breakdown"],
                        "properties": {
                            "amount": {"type": "number"},
                            "breakdown": {
                                "type": "object",
                                "additionalProperties": {"type": "number"}
                            }
                        }
                    },
                    "expense_total": {
                        "type": "object",
                        "required": ["gross", "deductible", "breakdown"],
                        "properties": {
                            "gross": {"type": "number"},
                            "deductible": {"type": "number"},
                            "breakdown": {
                                "type": "object",
                                "additionalProperties": {"type": "number"}
                            }
                        }
                    },
                    "net_rental_income": {"type": "number"},
                    "ring_fencing_applies": {"type": "boolean"},
                    "ring_fenced_loss": {"type": "number"}
                }
            },
            "pl_result": {
                "type": "object",
                "required": ["income", "expenses", "summary"],
                "properties": {
                    "income": {"type": "object"},
                    "expenses": {"type": "object"},
                    "summary": {
                        "type": "object",
                        "required": [
                            "total_income",
                            "total_expenses",
                            "total_deductions",
                            "net_rental_income"
                        ],
                        "properties": {
                            "total_income": {"type": "number"},
                            "total_expenses": {"type": "number"},
                            "total_deductions": {"type": "number"},
                            "interest_gross": {"type": "number"},
                            "interest_deductible_percentage": {"type": "number"},
                            "interest_deductible_amount": {"type": "number"},
                            "net_rental_income": {"type": "number"}
                        }
                    }
                }
            }
        }
    }
}
```

**Additional Checks (from NZ Tax Research):**

| Check | Implementation |
|-------|----------------|
| Mathematical Cross-foot | Validate all sub-totals add correctly |
| Source Tracing | REQUIRE source_code for every P&L line |
| Bank Total Reconciliation | Compare bank debits+credits to P&L totals |
| PM Statement Reconciliation | Validate PM rent + expenses = bank receipts |
| Year 1 Settlement Verification | Confirm all settlement items captured |
| GST Consistency | Validate GST-inclusive/exclusive treatment |
| Ring-Fencing Compliance | IF loss, calculate and flag ring-fenced amount |
| IR3R Field Mapping | Validate amounts map correctly to form fields |

---

### Phase 2D: QA Review

**Scope Boundaries:**
- ✅ Cross-validate ALL phase outputs
- ✅ Verify against raw source data
- ✅ Check for false positives before flagging
- ✅ Generate comprehensive QA report
- ❌ Do NOT modify values (flag only)

**Tool Use Schema:**
```python
QA_REVIEW_SCHEMA = {
    "name": "qa_review",
    "description": "QA validation of tax return workings",
    "input_schema": {
        "type": "object",
        "required": ["structure_checks", "calculation_checks", "consistency_checks", "qa_result"],
        "properties": {
            "structure_checks": {
                "type": "object",
                "required": ["all_categories_present", "all_sources_valid", "all_transactions_traced"],
                "properties": {
                    "all_categories_present": {"type": "boolean"},
                    "missing_categories": {"type": "array", "items": {"type": "string"}},
                    "all_sources_valid": {"type": "boolean"},
                    "invalid_sources": {"type": "array", "items": {"type": "string"}},
                    "all_transactions_traced": {"type": "boolean"},
                    "untraced_transactions": {"type": "array"}
                }
            },
            "calculation_checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["category", "expected", "actual", "variance", "is_valid"],
                    "properties": {
                        "category": {"type": "string"},
                        "expected": {"type": "number"},
                        "actual": {"type": "number"},
                        "variance": {"type": "number"},
                        "is_valid": {"type": "boolean"},
                        "explanation": {"type": "string"}
                    }
                }
            },
            "consistency_checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["check_name", "passed", "details"],
                    "properties": {
                        "check_name": {
                            "type": "string",
                            "enum": [
                                "interest_rate_correct_for_year",
                                "new_build_ccc_verified",
                                "year1_settlement_complete",
                                "bc_reserve_excluded",
                                "depreciation_prorated",
                                "accounting_fees_included",
                                "bank_contribution_searched"
                            ]
                        },
                        "passed": {"type": "boolean"},
                        "details": {"type": "string"},
                        "fix_required": {"type": "string"}
                    }
                }
            },
            "false_positive_prevention": {
                "type": "array",
                "description": "Checks before flagging errors",
                "items": {
                    "type": "object",
                    "required": ["potential_issue", "verified_against_source", "legitimate_explanation", "should_flag"],
                    "properties": {
                        "potential_issue": {"type": "string"},
                        "verified_against_source": {"type": "boolean"},
                        "legitimate_explanation": {"type": "string"},
                        "should_flag": {"type": "boolean"}
                    }
                }
            },
            "qa_result": {
                "type": "object",
                "required": ["overall_status", "issues_found", "fixes_required"],
                "properties": {
                    "overall_status": {
                        "type": "string",
                        "enum": ["pass", "issues_found", "critical_errors"]
                    },
                    "issues_found": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["severity", "category", "issue", "fix"],
                            "properties": {
                                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                                "category": {"type": "string"},
                                "issue": {"type": "string"},
                                "current_value": {"type": "number"},
                                "expected_value": {"type": "number"},
                                "fix": {"type": "string"}
                            }
                        }
                    },
                    "fixes_required": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["category", "item_key", "old_value", "new_value", "reason"],
                            "properties": {
                                "category": {"type": "string"},
                                "item_key": {"type": "string"},
                                "old_value": {"type": "number"},
                                "new_value": {"type": "number"},
                                "reason": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
    }
}
```

---

### Phase 2E: Reconciliation

**Scope Boundaries:**
- ✅ Apply fixes from QA Review
- ✅ Recalculate affected totals
- ✅ Generate final output
- ✅ Create audit trail

**Tool Use Schema:**
```python
RECONCILIATION_SCHEMA = {
    "name": "reconcile_workings",
    "description": "Final reconciliation and output generation",
    "input_schema": {
        "type": "object",
        "required": ["fixes_applied", "final_workings", "audit_trail"],
        "properties": {
            "fixes_applied": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["fix_id", "applied", "before", "after"],
                    "properties": {
                        "fix_id": {"type": "string"},
                        "applied": {"type": "boolean"},
                        "before": {"type": "number"},
                        "after": {"type": "number"},
                        "reason": {"type": "string"}
                    }
                }
            },
            "final_workings": {
                "type": "object",
                "description": "Complete TaxReturnWorkingsData structure"
            },
            "audit_trail": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["phase", "action", "timestamp", "result"],
                    "properties": {
                        "phase": {"type": "string"},
                        "action": {"type": "string"},
                        "timestamp": {"type": "string"},
                        "input_hash": {"type": "string"},
                        "output_hash": {"type": "string"},
                        "result": {"type": "string"}
                    }
                }
            }
        }
    }
}
```

---

## File Changes Required

### New Files to Create

| File | Purpose |
|------|---------|
| `app/services/phase2_ai_brain/schemas/interest_schema.py` | Tool Use schema for interest calculation |
| `app/services/phase2_ai_brain/schemas/expense_schema.py` | Tool Use schema for expense coding |
| `app/services/phase2_ai_brain/schemas/pl_schema.py` | Tool Use schema for P&L completion |
| `app/services/phase2_ai_brain/schemas/qa_schema.py` | Tool Use schema for QA review |
| `app/services/phase2_ai_brain/phases/interest_calculator.py` | Phase 2A implementation |
| `app/services/phase2_ai_brain/phases/expense_coder.py` | Phase 2B implementation |
| `app/services/phase2_ai_brain/phases/pl_completer.py` | Phase 2C implementation |
| `app/services/phase2_ai_brain/phases/qa_reviewer.py` | Phase 2D implementation |
| `app/services/phase2_ai_brain/phases/reconciler.py` | Phase 2E implementation |
| `app/services/phase2_ai_brain/prompts/interest_prompt.py` | Interest calculation prompt |
| `app/services/phase2_ai_brain/prompts/expense_prompt.py` | Expense coding prompt |
| `app/services/phase2_ai_brain/prompts/pl_prompt.py` | P&L completion prompt |
| `app/services/phase2_ai_brain/prompts/qa_prompt.py` | QA review prompt |

### Files to Modify

| File | Changes |
|------|---------|
| `app/services/phase2_ai_brain/brain.py` | Refactor to orchestrate 5 phases |
| `app/services/phase1_document_intake/claude_client.py` | Add Tool Use support |
| `app/config.py` | Add phase configuration settings |

---

## Implementation Steps

### Step 1: Create Tool Use Infrastructure
1. Create `schemas/` directory with all schema definitions
2. Modify `claude_client.py` to support Tool Use calls
3. Add Tool Use validation helpers

### Step 2: Implement Phase 2A (Interest Calculation)
1. Create `phases/interest_calculator.py`
2. Create `prompts/interest_prompt.py` with Romulus logic
3. Add additional checks from NZ tax research
4. Write unit tests

### Step 3: Implement Phase 2B (Expense Coding)
1. Create `phases/expense_coder.py`
2. Create `prompts/expense_prompt.py` with Romulus logic
3. Add additional checks from NZ tax research
4. Write unit tests

### Step 4: Implement Phase 2C (P&L Completion)
1. Create `phases/pl_completer.py`
2. Create `prompts/pl_prompt.py` with Romulus logic
3. Add bank contribution check enforcement
4. Write unit tests

### Step 5: Implement Phase 2D (QA Review)
1. Create `phases/qa_reviewer.py`
2. Create `prompts/qa_prompt.py` with false positive prevention
3. Add cross-phase validation
4. Write unit tests

### Step 6: Implement Phase 2E (Reconciliation)
1. Create `phases/reconciler.py`
2. Implement fix application logic
3. Generate audit trail
4. Write unit tests

### Step 7: Integrate into Brain Orchestrator
1. Refactor `brain.py` to call phases sequentially
2. Pass outputs between phases
3. Handle errors and rollbacks
4. Write integration tests

### Step 8: Testing & Validation
1. Run against existing test cases
2. Compare results to current implementation
3. Measure consistency (same input = same output)
4. Performance testing (acceptable latency)

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Consistency | Same input produces identical output 100% of time |
| Bank contribution detection | 100% accuracy when present in documents |
| Interest calculation accuracy | Within $1 of manual calculation |
| False positive rate | <5% (verified against source data) |
| Processing time | <60 seconds for typical return |
| Test coverage | >90% for new code |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Claude API rate limits | Implement exponential backoff, batch where possible |
| Tool Use parsing errors | Strict schema validation, fallback to current approach |
| Increased latency (5 calls vs 1) | Parallel processing where phases are independent |
| Regression in existing functionality | Comprehensive test suite, feature flag for rollout |
| Cost increase (more API calls) | Monitor token usage, optimize prompts |

---

## Rollout Plan

### Phase 1: Shadow Mode (Week 1-2)
- Run new implementation alongside existing
- Compare outputs, log discrepancies
- No user-facing changes

### Phase 2: Beta (Week 3-4)
- Enable for select test returns
- Gather feedback
- Fix issues

### Phase 3: General Availability (Week 5+)
- Full rollout
- Monitor for issues
- Iterate based on feedback

---

## Appendix: Checkpoint Reasoning Template

Each phase uses explicit checkpoints that force the AI to reason step-by-step:

```
CHECKPOINT 1: IDENTIFY
- What items did I find?
- What data source did I use?
- Is anything missing?

CHECKPOINT 2: CLASSIFY
- For each item: What category does it belong to?
- What rule applies?
- What is my reasoning?

CHECKPOINT 3: VALIDATE
- Does my classification make sense?
- Did I apply rules consistently?
- Are there edge cases I should consider?

CHECKPOINT 4: CALCULATE
- What is the final amount?
- Show my working
- Cross-check against other sources
```

This explicit checkpoint structure ensures:
1. **Traceability** - Every decision has a documented reason
2. **Consistency** - Same rules applied every time
3. **Auditability** - Client can see exactly how figures were derived
4. **Debuggability** - Easy to identify where errors occur
