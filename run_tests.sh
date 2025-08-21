#!/bin/bash
# CIRISLens Test Runner

set -e

echo "========================================="
echo "CIRISLens Test Suite"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -q -r api/requirements.txt
pip install -q -r requirements-dev.txt

# Run unit tests
echo -e "\n${GREEN}Running Unit Tests...${NC}"
python -m pytest tests/test_manager_collector.py -v --tb=short

# Check if integration tests should run
if [ "$1" == "--integration" ]; then
    echo -e "\n${GREEN}Running Integration Tests...${NC}"
    python -m pytest tests/test_manager_collector.py -v -m integration --tb=short
fi

# Run coverage if requested
if [ "$1" == "--coverage" ]; then
    echo -e "\n${GREEN}Running Coverage Analysis...${NC}"
    python -m pytest tests/test_manager_collector.py --cov=api --cov-report=term-missing
fi

# Test the CLI tool
echo -e "\n${GREEN}Testing Manager Validator CLI...${NC}"
python tools/manager_validator.py --help > /dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ CLI tool is functional${NC}"
else
    echo -e "${RED}✗ CLI tool failed${NC}"
    exit 1
fi

echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}All tests completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"