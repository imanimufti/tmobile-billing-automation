#!/bin/bash
# Helper script to process a T-Mobile bill and start payment monitoring

set -e

if [ "$#" -lt 1 ]; then
    echo "Usage: ./process_bill.sh <path_to_bill.pdf>"
    echo "Example: ./process_bill.sh bills/SummaryBillMar2026.pdf"
    exit 1
fi

BILL_PATH="$1"

echo "========================================="
echo "T-Mobile Bill Processing"
echo "========================================="

# Step 1: Update Google Sheet
echo ""
echo "Step 1: Updating Google Sheet..."
python3 src/update_google_sheet.py "$BILL_PATH"

# Extract month from output or filename
MONTH=$(basename "$BILL_PATH" | grep -oE "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[0-9]{4}" | sed 's/\(.\{3\}\)\([0-9]\{2\}\)\([0-9]\{2\}\)/\1 \2/')

if [ -z "$MONTH" ]; then
    echo ""
    read -p "Enter the tab name for payment monitoring (e.g., 'Mar 26'): " MONTH
fi

# Step 2: Ask if user wants to start payment monitoring
echo ""
echo "========================================="
echo "Payment Monitoring"
echo "========================================="
read -p "Start payment monitoring for '$MONTH'? (y/n): " START_MONITOR

if [ "$START_MONITOR" = "y" ] || [ "$START_MONITOR" = "Y" ]; then
    echo ""
    echo "Starting payment monitor (Press Ctrl+C to stop)..."
    python3 src/monitor_venmo_payments.py "$MONTH" --watch --interval 300
else
    echo ""
    echo "You can manually start payment monitoring later with:"
    echo "  python3 src/monitor_venmo_payments.py '$MONTH' --watch"
fi
