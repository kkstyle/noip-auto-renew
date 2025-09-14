# No-IP Auto Renewer v2.0

🤖 **Advanced automated system for renewing free No-IP.com hosts**

## 🙏 Credits

This project is a **substantial derivation** based on the original work by [MineFartS/noip-renew](https://github.com/MineFartS/noip-renew/).

Version v2.0 represents a **complete rewrite** with modern architecture and technologies:
- **Playwright** replaces Selenium for better reliability
- **Native TOTP handling** without Google Apps Script dependencies
- **Modular architecture** with robust error handling
- **Configurable multi-channel notifications**
- **Structured logging** with automatic rotation

**Special thanks to [MineFartS](https://github.com/MineFartS) for the original idea and base implementation that inspired this project.**

---

This system uses:
- **Playwright** for reliable automated web navigation
- **Email notifications** for renewal updates
- **Native TOTP generation** for automatic OTP handling

## 📋 Requirements

- Python 3.8+
- Supported browsers: Chrome, Firefox, Safari, Edge
- No-IP.com account with 2FA enabled
- Email account for notifications (optional)
- TOTP secret key for 2FA

## 🚀 Installation

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/noip-auto-renewer-v2.git
cd noip-auto-renewer-v2
```

### 2. Install dependencies
```bash
pip install -r requirements_v2.txt
```

### 3. Install browsers for Playwright
```bash
playwright install
```

### 4. Configuration

The system will automatically create a `config_v2.json` file on first run. Edit this file with your credentials:

```json
{
  "noip": {
    "username": "your-email@example.com",
    "password": "your-noip-password",
    "totp_secret": "ABCD1234EFGH5678IJKL",
    "hosts": ["yourhost.ddns.net", "anotherhost.hopto.org"]
  },
  "browser": {
    "headless": true,
    "browser_type": "chromium",
    "timeout": 30000,
    "user_agent": "Mozilla/5.0..."
  },
  "notifications": {
    "email_enabled": true,
    "email_user": "your-email@gmail.com",
    "email_password": "your-app-password",
    "recipient_email": "recipient@example.com",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587
  },
  "retry": {
    "max_retries": 3,
    "base_delay": 1.0,
    "max_delay": 60.0,
    "exponential_base": 2.0,
    "jitter": true
  }
}
```

**⚠️ TOTP Configuration**: 
1. Enable 2FA on your No-IP account
2. During setup, save the secret key (not the QR code)
3. Enter this key in the `totp_secret` field

## 🎯 Usage

### Basic Execution
```bash
# Single execution of the renewal process
python noip_renewer_v2.py --run-once

# Standard execution (default mode)
python noip_renewer_v2.py

# Test notifications
python noip_renewer_v2.py --test-notifications

# Display all available options
python noip_renewer_v2.py --help
```

### Command Line Options
- `--run-once`: Execute renewal once and exit
- `--headless`: Run browser in headless mode (without GUI)
- `--browser`: Specify browser to use (chrome, firefox, safari, edge)
- `--config`: Specify custom configuration file
- `--test-notifications`: Test notification system
- `--help`: Show complete help

### Automatic Scheduling

**Windows (Task Scheduler):**
```batch
@echo off
cd /d "C:\path\to\noip-auto-renewer-v2"
python noip_renewer_v2.py --run-once
```

**Linux (Crontab):**
```bash
# Daily execution at 9:00 AM
0 9 * * * cd /path/to/noip-auto-renewer-v2 && python noip_renewer_v2.py --run-once
```

**Recommendations:**
- Run renewal **once per day**
- Always use the `--run-once` option for scheduled executions
- Configure email notifications to monitor status

## ⚙️ Features

- ✅ **Automatic renewal** of No-IP hosts with Playwright
- ✅ **2FA authentication** integrated with TOTP
- ✅ **Email notifications** for successes and failures
- ✅ **Robust error handling** with intelligent retries
- ✅ **Structured logging** with automatic rotation
- ✅ **Headless mode** for GUI-less servers
- ✅ **Multi-browser compatibility** (Chrome, Firefox, Safari, Edge)
- ✅ **Flexible and secure JSON configuration**

## 🔧 How It Works

1. **Configuration**: Reads credentials and settings from `config_v2.json`
2. **Authentication**: Logs into No-IP with username/password and two-factor authentication (2FA)
3. **Navigation**: Navigates to the host management page
4. **Renewal**: Finds and clicks renewal buttons for all expiring hosts
5. **Logging**: Records all operations in the log file
6. **Notifications**: Sends confirmation emails or alerts in case of errors

### Detailed Process

**Initialization Phase:**
- Loads configuration from JSON file
- Initializes logging system
- Prepares Playwright browser

**Authentication Phase:**
- Accesses No-IP login page
- Enters username and password
- Automatically handles TOTP code for two-factor authentication
- Verifies login success

**Renewal Phase:**
- Navigates to "My No-IP" section
- Identifies hosts that need renewal
- Executes renewal for each host found
- Handles any errors or timeouts

**Completion Phase:**
- Records results in log file
- Sends email notifications if configured
- Closes browser and terminates execution

### Log Files
All operations are logged in `noip_renewer_v2.log`:
```
2024-01-15 10:30:00 - INFO - === Starting No-IP Auto Renew v2.0 ===
2024-01-15 10:30:05 - INFO - Login successful
2024-01-15 10:30:10 - INFO - TOTP code generated successfully
2024-01-15 10:30:15 - INFO - Hosts renewed: 2/2
2024-01-15 10:30:20 - INFO - Notification email sent
```

### Exit Codes
- `0`: Success
- `1`: Error during execution

## 🛠️ Troubleshooting

### Error: "Browser not found"
```bash
# Install browsers for Playwright
playwright install
```

### Error: "TOTP generation failed"
- Verify that `totp_secret` is correct in `config_v2.json`
- Ensure the key is in base32 format
- Test the key with an authenticator app for confirmation

### Error: "Login failed"
- Verify username and password in `config_v2.json`
- Check that the No-IP account is active
- Verify that 2FA is enabled and configured correctly

### Error: "Email notification failed"
- Verify email credentials in `config_v2.json`
- For Gmail, use an **App Password** instead of the regular password
- Check your provider's SMTP settings

### Debug Mode
```bash
# Run in non-headless mode to see the browser
python noip_renewer_v2.py --debug
```

## 🔒 Security

- ⚠️ **Never share the `config_v2.json` file** - it contains sensitive credentials
- ⚠️ **Keep the TOTP key private**
- ✅ Use **App Password** for Gmail instead of the main password
- ✅ Consider using environment variables for critical credentials
- ✅ Log files do not contain sensitive credentials

## 📝 Notes

- Free No-IP hosts expire every **30 days**
- It's recommended to run the script **daily** using a scheduler
- Renewal can be performed up to **7 days before** expiration
- The system supports **multiple hosts** per account
- Compatible with all **modern browsers** via Playwright

## 🆕 What's New in v2.0

- **Completely rewritten architecture** for greater reliability
- **Playwright** replaces Selenium for better performance
- **Native TOTP handling** without external dependencies
- **Configurable email notifications**
- **Intelligent retries** with exponential backoff
- **Structured logging** with automatic rotation
- **More flexible and secure JSON configuration**

## 📄 License

This project is released under **MIT License**. See the LICENSE file for details.

⭐ **If this project has been useful to you, leave a star on GitHub!**

**⚠️ Disclaimer**: This script is provided "as is" without warranties. Use at your own risk. Make sure to comply with No-IP.com terms of service.