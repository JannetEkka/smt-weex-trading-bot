#!/bin/bash
# SMT Trading Daemon V3 - VM Setup Script
# Run this on the VM to set up the daemon

set -e

echo "=========================================="
echo "SMT Trading Daemon V3 - Setup"
echo "=========================================="

# Variables
REPO_DIR="$HOME/smt-weex-trading-bot/v3"
SERVICE_NAME="smt-trading"

# 1. Check if we're in the right directory
if [ ! -d "$REPO_DIR" ]; then
    echo "ERROR: Repository not found at $REPO_DIR"
    echo "Clone it first: git clone https://github.com/JannetEkka/smt-weex-trading-bot.git"
    exit 1
fi

cd "$REPO_DIR"

# 2. Create necessary directories
echo "Creating directories..."
mkdir -p logs ai_logs

# 3. Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements-v3.txt

# 4. Create .env file if not exists
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "IMPORTANT: Edit .env file with your actual credentials!"
fi

# 5. Set up systemd service
echo "Setting up systemd service..."

# Update service file with correct username
CURRENT_USER=$(whoami)
sed -i "s/jannetekka/$CURRENT_USER/g" smt-trading.service

# Copy service file
sudo cp smt-trading.service /etc/systemd/system/${SERVICE_NAME}.service

# Reload systemd
sudo systemctl daemon-reload

# 6. Authenticate with Google Cloud
echo "Checking Google Cloud authentication..."
if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "Running gcloud auth..."
    gcloud auth application-default login
fi

# 7. Test the daemon (dry run)
echo "Testing daemon import..."
python3 -c "from smt_daemon_v3 import state; print('Import OK')"

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Commands:"
echo "  Start daemon:   sudo systemctl start $SERVICE_NAME"
echo "  Stop daemon:    sudo systemctl stop $SERVICE_NAME"
echo "  Restart:        sudo systemctl restart $SERVICE_NAME"
echo "  Status:         sudo systemctl status $SERVICE_NAME"
echo "  Enable on boot: sudo systemctl enable $SERVICE_NAME"
echo "  View logs:      tail -f logs/daemon_$(date +%Y%m%d).log"
echo "  Journal logs:   journalctl -u $SERVICE_NAME -f"
echo ""
echo "Test mode (no real trades):"
echo "  SMT_TEST_MODE=true python3 smt_daemon_v3.py"
echo ""
echo "IMPORTANT: Before starting, verify:"
echo "  1. .env file has correct credentials"
echo "  2. Run a test first: python3 smt_daemon_v3.py --test"
echo "  3. Check balance on WEEX"
echo ""
