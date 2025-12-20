#!/bin/bash

# Property Tax Agent Test Runner
# This script runs the comprehensive test suite with various options

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
TEST_TYPE="all"
COVERAGE=false
VERBOSE=false
HTML_REPORT=false
MARKERS=""
PARALLEL=false

# Function to print colored output
print_color() {
    color=$1
    message=$2
    echo -e "${color}${message}${NC}"
}

# Function to print usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -t, --type TYPE       Test type: all, unit, integration, manual (default: all)"
    echo "  -c, --coverage        Generate coverage report"
    echo "  -v, --verbose         Verbose output"
    echo "  -h, --html            Generate HTML test report"
    echo "  -m, --markers MARKERS Pytest markers to run (e.g., 'not slow')"
    echo "  -p, --parallel        Run tests in parallel"
    echo "  -s, --setup           Run setup verification before tests"
    echo "  --help                Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Run all tests"
    echo "  $0 -t unit -c         # Run unit tests with coverage"
    echo "  $0 -t integration -v  # Run integration tests verbosely"
    echo "  $0 -m 'not slow' -p   # Run non-slow tests in parallel"
    exit 0
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            TEST_TYPE="$2"
            shift 2
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--html)
            HTML_REPORT=true
            shift
            ;;
        -m|--markers)
            MARKERS="$2"
            shift 2
            ;;
        -p|--parallel)
            PARALLEL=true
            shift
            ;;
        -s|--setup)
            RUN_SETUP=true
            shift
            ;;
        --help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Header
print_color "$BLUE" "========================================="
print_color "$BLUE" "Property Tax Agent Test Runner"
print_color "$BLUE" "========================================="
echo ""

# Check if we're in the right directory
if [ ! -f "pyproject.toml" ]; then
    print_color "$RED" "Error: Must run from project root directory"
    exit 1
fi

# Run setup verification if requested
if [ "$RUN_SETUP" = true ]; then
    print_color "$YELLOW" "Running setup verification..."
    poetry run python scripts/verify_setup.py
    if [ $? -ne 0 ]; then
        print_color "$RED" "Setup verification failed!"
        exit 1
    fi
    echo ""
fi

# Build pytest command
CMD="poetry run pytest"

# Add test directory based on type
case $TEST_TYPE in
    all)
        CMD="$CMD tests/"
        print_color "$GREEN" "Running all tests..."
        ;;
    unit)
        CMD="$CMD tests/test_phase3_integration.py::TestModels tests/test_phase3_integration.py::TestYAMLRules tests/test_phase3_integration.py::TestServices"
        print_color "$GREEN" "Running unit tests..."
        ;;
    integration)
        CMD="$CMD tests/test_phase3_integration.py::TestAPI tests/test_phase3_integration.py::TestIntegration"
        print_color "$GREEN" "Running integration tests..."
        ;;
    manual)
        CMD="$CMD tests/test_manual_checklist.py"
        print_color "$GREEN" "Running manual test checklist generation..."
        ;;
    *)
        print_color "$RED" "Unknown test type: $TEST_TYPE"
        usage
        ;;
esac

# Add coverage if requested
if [ "$COVERAGE" = true ]; then
    CMD="$CMD --cov=app --cov-report=term-missing --cov-report=html"
    print_color "$YELLOW" "Coverage reporting enabled"
fi

# Add verbose flag if requested
if [ "$VERBOSE" = true ]; then
    CMD="$CMD -v"
fi

# Add markers if specified
if [ -n "$MARKERS" ]; then
    CMD="$CMD -m \"$MARKERS\""
    print_color "$YELLOW" "Using markers: $MARKERS"
fi

# Add parallel execution if requested
if [ "$PARALLEL" = true ]; then
    CMD="$CMD -n auto"
    print_color "$YELLOW" "Running tests in parallel"
fi

# Add HTML report if requested
if [ "$HTML_REPORT" = true ]; then
    REPORT_DIR="reports/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$REPORT_DIR"
    CMD="$CMD --html=$REPORT_DIR/report.html --self-contained-html"
    print_color "$YELLOW" "HTML report will be saved to: $REPORT_DIR/report.html"
fi

# Show deprecation warnings
CMD="$CMD -W default::DeprecationWarning"

echo ""
print_color "$BLUE" "Command: $CMD"
echo ""

# Run tests
eval $CMD
TEST_RESULT=$?

echo ""
print_color "$BLUE" "========================================="

# Check results
if [ $TEST_RESULT -eq 0 ]; then
    print_color "$GREEN" "All tests passed!"

    # Show coverage report location if generated
    if [ "$COVERAGE" = true ]; then
        print_color "$YELLOW" "Coverage report: htmlcov/index.html"
    fi

    # Show HTML report location if generated
    if [ "$HTML_REPORT" = true ]; then
        print_color "$YELLOW" "Test report: $REPORT_DIR/report.html"
    fi
else
    print_color "$RED" "Tests failed!"
    exit 1
fi

print_color "$BLUE" "========================================="

# Generate manual testing checklist if requested
if [ "$TEST_TYPE" = "manual" ] || [ "$TEST_TYPE" = "all" ]; then
    echo ""
    print_color "$YELLOW" "Generating manual testing checklist..."
    poetry run python -m tests.test_manual_checklist
    if [ $? -eq 0 ]; then
        print_color "$GREEN" "Manual testing checklist generated: docs/testing/manual_testing_checklist.md"
    fi
fi

# Cleanup
if [ -d ".pytest_cache" ]; then
    rm -rf .pytest_cache
fi

# Optional: Open coverage report in browser
if [ "$COVERAGE" = true ] && command -v open &> /dev/null; then
    read -p "Open coverage report in browser? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        open htmlcov/index.html
    fi
fi

exit 0