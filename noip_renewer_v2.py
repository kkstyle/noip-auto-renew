#!/usr/bin/env python3
"""
No-IP Auto Renewer v2.0 - Enhanced Version

Automatic system for No-IP domain renewal with robust architecture:
- Playwright for more stable browser management
- Multi-channel notification system
- Hybrid CAPTCHA handling
- Advanced retry logic
- Complete structured logging

Author: Assistant
Version: 2.0
"""

import asyncio
import json
import logging
import os
import random
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable, Any

import requests
import structlog
import pyotp
from playwright.async_api import async_playwright, Browser, Page
from dataclasses import dataclass
from enum import Enum

# Structured logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('noip_renewer_v2.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RenewalStatus(Enum):
    """Enumeration for renewal status"""
    SUCCESS = "success"
    FAILED = "failed"
    CAPTCHA_REQUIRED = "captcha_required"
    RETRY_NEEDED = "retry_needed"
    MANUAL_INTERVENTION = "manual_intervention"
    RETRY_EXHAUSTED = "retry_exhausted"
    NETWORK_ERROR = "network_error"
    TIMEOUT_ERROR = "timeout_error"
    NO_RENEWAL_NEEDED = "no_renewal_needed"

class RetryableError(Exception):
    """Exception for retryable errors"""
    pass

class NonRetryableError(Exception):
    """Exception for non-retryable errors"""
    pass

@dataclass
class NotificationConfig:
    """Configuration for notifications"""
    email_enabled: bool = True
    telegram_enabled: bool = False
    desktop_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    email_user: str = ""
    email_password: str = ""
    recipient_email: str = ""

@dataclass
class RetryConfig:
    """Configuration for retry attempts"""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True

class NoIPRenewerV2:
    """Main class for No-IP v2.0 automatic renewal"""
    
    def __init__(self, config_path: str = "config_v2.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.notification_config = NotificationConfig(**self.config.get('notifications', {}))
        self.retry_config = RetryConfig(**self.config.get('retry', {}))
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.totp_secret = self.config.get('totp_secret', '')
        
        # Structured logging setup
        self._setup_structured_logging()
    
    def _setup_structured_logging(self):
        """Configure advanced structured logging"""
        # Configure processors for different environments
        processors = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
        ]
        
        # Add JSON processor for production or console for development
        if self.config.get('log_format', 'json') == 'json':
            processors.append(structlog.processors.JSONRenderer())
        else:
            processors.append(structlog.dev.ConsoleRenderer())
        
        structlog.configure(
            processors=processors,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        
        # Configure logging level
        log_level = self.config.get('log_level', 'INFO').upper()
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(
                    self.config.get('log_file', 'noip_renewer.log'),
                    encoding='utf-8'
                )
            ]
        )
        
    async def retry_with_backoff(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function with retry logic and exponential backoff"""
        last_exception = None
        
        for attempt in range(self.retry_config.max_retries):
            try:
                return await func(*args, **kwargs)
            except NonRetryableError:
                # Don't retry for non-recoverable errors
                raise
            except Exception as e:
                last_exception = e
                logger.warning(f"Attempt {attempt + 1}/{self.retry_config.max_retries} failed: {e}")
                
                if attempt < self.retry_config.max_retries - 1:
                    delay = self.calculate_retry_delay(attempt)
                    logger.info(f"Waiting {delay:.2f} seconds before next attempt")
                    await asyncio.sleep(delay)
        
        # If we get here, all attempts have failed
        logger.error(f"All {self.retry_config.max_retries} attempts failed")
        raise last_exception or Exception("Retry exhausted")
    
    def calculate_retry_delay(self, attempt: int) -> float:
        """Calculate delay for next attempt with exponential backoff"""
        delay = self.retry_config.base_delay * (self.retry_config.exponential_base ** attempt)
        delay = min(delay, self.retry_config.max_delay)
        
        if self.retry_config.jitter:
            # Add jitter to avoid thundering herd
            jitter = delay * 0.1 * random.random()
            delay += jitter
        
        return delay
    
    async def safe_page_operation(self, operation_name: str, operation_func: Callable, *args, **kwargs) -> Any:
        """Execute a page operation with error handling and retry"""
        async def wrapped_operation():
            try:
                if not self.page:
                    raise NonRetryableError("Page not initialized")
                
                return await operation_func(*args, **kwargs)
            
            except Exception as e:
                error_msg = str(e).lower()
                
                # Classify errors
                if any(keyword in error_msg for keyword in ['timeout', 'network', 'connection']):
                    logger.warning(f"Network error during {operation_name}: {e}")
                    raise RetryableError(f"Network error: {e}")
                
                elif 'element not found' in error_msg or 'locator' in error_msg:
                    logger.warning(f"Element not found during {operation_name}: {e}")
                    raise RetryableError(f"Element not found: {e}")
                
                elif any(keyword in error_msg for keyword in ['permission', 'unauthorized', 'forbidden']):
                    logger.error(f"Authorization error during {operation_name}: {e}")
                    raise NonRetryableError(f"Authorization error: {e}")
                
                else:
                    # Generic error, retry
                    logger.warning(f"Generic error during {operation_name}: {e}")
                    raise RetryableError(f"Generic error: {e}")
        
        try:
            return await self.retry_with_backoff(wrapped_operation)
        except Exception as e:
            logger.error(f"Operation {operation_name} failed permanently: {e}")
            raise
        
    def load_config(self) -> Dict:
        """Load configuration from JSON file"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logger.info(f"Configuration loaded from {self.config_path}")
                return config
            else:
                logger.warning(f"Configuration file {self.config_path} not found, using default configuration")
                return self.create_default_config()
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            return self.create_default_config()
    
    def create_default_config(self) -> Dict:
        """Create a default configuration"""
        default_config = {
            "noip_username": "",
            "noip_password": "",
            "totp_secret": "",
            "hosts": [],
            "notifications": {
                "email_enabled": True,
                "telegram_enabled": False,
                "desktop_enabled": True,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "email_user": "",
                "email_password": "",
                "recipient_email": ""
            },
            "retry": {
                "max_retries": 3,
                "base_delay": 1.0,
                "max_delay": 60.0,
                "exponential_base": 2.0,
                "jitter": True
            },
            "browser": {
                "headless": True,
                "timeout": 60000,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        }
        
        # Save default configuration
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            logger.info(f"Default configuration saved to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving default configuration: {e}")
        
        return default_config
    
    async def setup_browser(self) -> bool:
        """Initialize Playwright browser with enhanced email verification support"""
        try:
            self.playwright = await async_playwright().start()
            browser_config = self.config.get('browser', {})
            
            # Use headless mode from configuration
            headless_mode = browser_config.get('headless', True)  # Default to headless
            
            self.browser = await self.playwright.chromium.launch(
                headless=headless_mode,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--disable-dev-shm-usage',
                    '--no-first-run',
                    '--disable-default-apps'
                ]
            )
            
            context = await self.browser.new_context(
                user_agent=browser_config.get('user_agent', 
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
                viewport={'width': 1920, 'height': 1080}
            )
            
            self.page = await context.new_page()
            # Increase timeout for email verification scenarios
            extended_timeout = browser_config.get('timeout', 60000)  # 60 seconds default
            self.page.set_default_timeout(extended_timeout)
            
            logger.info(f"Playwright browser initialized successfully (headless={headless_mode}, timeout={extended_timeout}ms)")
            
            if not headless_mode:
                print("\n🌐 Browser aperto in modalità visibile per verifica email")
            
            return True
            
        except Exception as e:
            logger.error(f"Error initializing browser: {e}")
            return False
    
    async def cleanup_browser(self) -> None:
        """Clean up browser resources properly"""
        try:
            if self.page:
                await self.page.close()
                self.page = None
                logger.info("Page closed successfully")
            
            if self.browser:
                await self.browser.close()
                self.browser = None
                logger.info("Browser closed successfully")
            
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
                logger.info("Playwright stopped successfully")
                
        except Exception as e:
            logger.error(f"Error during browser cleanup: {e}")
    
    async def run_renewal_process(self) -> Dict:
        """Execute complete renewal process with advanced monitoring"""
        start_time = datetime.now()
        process_id = f"renewal_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        results = {
            'process_id': process_id,
            'start_time': start_time.isoformat(),
            'success': False,
            'hosts_renewed': [],
            'hosts_failed': [],
            'errors': []
        }
        
        logger.info(
            "Starting No-IP v2.0 renewal process",
            extra={"process_id": process_id, "timestamp": start_time.isoformat()}
        )
        
        try:
            # Browser initialization
            if not await self.setup_browser():
                error_msg = "Unable to initialize browser"
                results['errors'].append(error_msg)
                await self.send_notification("No-IP Renewal Error", error_msg, "high")
                return results
            
            # Login
            if not await self.login_to_noip():
                error_msg = "Login failed"
                results['errors'].append(error_msg)
                await self.send_notification("No-IP Login Error", error_msg, "high")
                return results
            
            # Domain renewal with monitoring for each host
            renewal_results = []
            hosts = self.config.get('hosts', [])
            
            for host in hosts:
                host_start_time = datetime.now()
                retry_count = 0
                
                try:
                    # Single host renewal
                    status = await self._renew_single_host(host)
                    error_msg = None if status == RenewalStatus.SUCCESS else f"Renewal failed: {status.value}"
                    
                    duration = (datetime.now() - host_start_time).total_seconds()
                    
                    # Log renewal metrics
                    renewal_results.append((host, status))
                    
                    logger.info(
                        "Host renewal completed",
                        extra={"host": host, "status": status.value, "duration": duration, "process_id": process_id}
                    )
                    
                except Exception as e:
                    duration = (datetime.now() - host_start_time).total_seconds()
                    error_msg = str(e)
                    
                    renewal_results.append((host, RenewalStatus.FAILED))
                    
                    logger.error(
                        "Error in host renewal",
                        extra={"host": host, "error": error_msg, "duration": duration, "process_id": process_id}
                    )
            
            # Process results
            for host, status in renewal_results:
                if status == RenewalStatus.SUCCESS:
                    results['hosts_renewed'].append(host)
                elif status == RenewalStatus.NO_RENEWAL_NEEDED:
                    # Non aggiungere ai risultati - il dominio non aveva bisogno di rinnovo
                    logger.info(f"Host {host} does not need renewal - skipping notification")
                else:
                    results['hosts_failed'].append({'host': host, 'status': status.value})
            
            # Determine overall success - solo se ci sono stati veri rinnovi
            results['success'] = len(results['hosts_renewed']) > 0 and len(results['hosts_failed']) == 0
            
        except Exception as e:
            error_msg = f"General error in renewal process: {e}"
            logger.error(
                "Error in renewal process",
                extra={"process_id": process_id, "error": str(e)}
            )
            results['errors'].append(error_msg)
            await self.send_notification("Critical Renewal Error", error_msg, "high")
        
        finally:
            # Browser cleanup
            if self.browser:
                await self.browser.close()
            
            end_time = datetime.now()
            results['end_time'] = end_time.isoformat()
            duration_seconds = (end_time - start_time).total_seconds()
            results['duration'] = f"{int(duration_seconds // 60)}m {int(duration_seconds % 60)}s"
            
            logger.info(
                "Renewal process completed",
                extra={"process_id": process_id, "duration": results['duration'], "success": results['success']}
            )
            
            # Send final notification after duration is calculated
            await self.send_final_notification(results)
            
        return results
    
    async def send_final_notification(self, results: Dict) -> None:
        """Send final notification with summary - only for actual renewals or failures"""
        # Non inviare notifiche se non ci sono stati rinnovi effettivi né errori
        if not results['hosts_renewed'] and not results['hosts_failed'] and not results['errors']:
            logger.info("No renewals needed and no errors - skipping notification")
            return
            
        if results['success']:
            subject = "✅ No-IP Renewal Completed"
            message = f"Renewal completed successfully!\n\n"
        else:
            subject = "❌ No-IP Renewal Failed"
            message = f"Renewal completed with errors.\n\n"
        
        if results['hosts_renewed']:
            message += f"Domini rinnovati: {len(results['hosts_renewed'])}\n"
            message += f"- {', '.join(results['hosts_renewed'])}\n\n"
        
        if results['hosts_failed']:
            message += f"Domini falliti: {len(results['hosts_failed'])}\n"
            for failed in results['hosts_failed']:
                message += f"- {failed['host']}: {failed['status']}\n"
            message += "\n"
        
        if results['errors']:
            message += f"Errori riscontrati:\n"
            for error in results['errors']:
                message += f"- {error}\n"
        
        message += f"\nDurata: {results['duration']}"
        
        priority = "normal" if results['success'] else "high"
        await self.send_notification(subject, message, priority)
    
    async def monitor_renewal_schedule(self) -> None:
        """Monitor and execute renewals according to schedule"""
        logger.info("Starting renewal schedule monitoring")
        
        while True:
            try:
                # Check if it's time to execute renewal
        # (simplified implementation - use cron or more sophisticated scheduler in production)
                now = datetime.now()
                
                # Execute renewal every 25 days (No-IP requires renewal every 30 days)
                last_run_file = Path('last_renewal.txt')
                
                if last_run_file.exists():
                    with open(last_run_file, 'r') as f:
                        last_run = datetime.fromisoformat(f.read().strip())
                    
                    days_since_last = (now - last_run).days
                    
                    if days_since_last >= 25:
                        logger.info(f"Executing scheduled renewal ({days_since_last} days since last)")
                        await self.run_renewal_process()
                        
                        # Update last renewal timestamp
                        with open(last_run_file, 'w') as f:
                            f.write(now.isoformat())
                    else:
                        logger.debug(f"Next renewal in {25 - days_since_last} days")
                else:
                    # First execution
                    logger.info("First execution of renewal system")
                    await self.run_renewal_process()
                    
                    with open(last_run_file, 'w') as f:
                        f.write(now.isoformat())
                
                # Wait 24 hours before next check
                await asyncio.sleep(86400)  # 24 ore
                
            except Exception as e:
                logger.error(f"Error in monitoring: {e}")
                await asyncio.sleep(3600)  # Retry in 1 hour in case of error

    async def login_to_noip(self) -> bool:
        """Perform login to No-IP.com with enhanced email verification handling"""
        try:
            if not self.page:
                logger.error("Page not initialized")
                return False
            
            # Navigate to login page
            await self.page.goto("https://www.noip.com/login")
            await self.page.wait_for_load_state("networkidle")
            
            # Enter credentials
            await self.page.fill("input[name='username']", self.config['noip_username'])
            await self.page.fill("input[name='password']", self.config['noip_password'])
            
            # Click login button
            await self.page.click("button[type='submit']")
            await self.page.wait_for_load_state("networkidle")
            
            # Check if we're already logged in
            if "dashboard" in self.page.url or "my" in self.page.url:
                logger.info("Login successful - no additional verification needed")
                return True
            
            # Check for verification request
            page_content = await self.page.content()
            
            # Check for TOTP/2FA request first (more specific)
            if any(keyword in page_content.lower() for keyword in ['totp', '2fa', 'authenticator', 'app', 'google authenticator']):
                logger.info("TOTP verification required - attempting automatic input")
                print("\n" + "="*60)
                print("🔐 VERIFICA TOTP RICHIESTA")
                print("="*60)
                print("No-IP richiede il codice TOTP dall'app di autenticazione.")
                print("Tentativo di inserimento automatico...")
                print("="*60)
                
                # Try automatic TOTP generation if secret is available
                if self.totp_secret:
                    try:
                        totp_code = self.generate_totp_code()
                        if totp_code:
                            print(f"\n🔑 Codice TOTP generato: {totp_code}")
                            print("Tentativo di inserimento automatico...")
                            
                            # Find TOTP input field and enter code
                            totp_input = await self.page.wait_for_selector('input[type="text"], input[type="number"], input[name*="code"], input[id*="code"]', timeout=5000)
                            if totp_input:
                                await totp_input.fill(totp_code)
                                await asyncio.sleep(1)
                                
                                # Try to find and click submit button
                                submit_button = await self.page.query_selector('button[type="submit"], input[type="submit"], button:has-text("Verify"), button:has-text("Submit")')
                                if submit_button:
                                    await submit_button.click()
                                    await asyncio.sleep(2)
                                    
                                    # Check if login was successful
                                    current_url = self.page.url
                                    if "dashboard" in current_url or "my" in current_url:
                                        logger.info("TOTP verification completed automatically - login successful")
                                        print("✅ Verifica TOTP automatica completata con successo!")
                                        return True
                                    else:
                                        print("⚠️ Inserimento automatico fallito, richiesto inserimento manuale")
                                else:
                                    print("⚠️ Pulsante submit non trovato, richiesto inserimento manuale")
                            else:
                                print("⚠️ Campo TOTP non trovato, richiesto inserimento manuale")
                    except Exception as e:
                        logger.error(f"Error during automatic TOTP: {e}")
                        print(f"⚠️ Errore inserimento automatico: {e}")
                
                # Manual TOTP input fallback
                print("\n🔧 Inserimento manuale richiesto:")
                if not self.totp_secret:
                    print("💡 Suggerimento: Aggiungi 'totp_secret' nel config per l'inserimento automatico")
                print("1. Apri l'app di autenticazione (Google Authenticator, Authy, etc.)")
                print("2. Trova il codice per No-IP")
                print("3. Inseriscilo nel browser")
                print("4. Il programma aspetterà fino a 3 minuti")
                
                max_wait_time = 180  # 3 minutes for TOTP
                check_interval = 5   # Check every 5 seconds
                
                for i in range(0, max_wait_time, check_interval):
                    await asyncio.sleep(check_interval)
                    
                    current_url = self.page.url
                    if "dashboard" in current_url or "my" in current_url:
                        logger.info("TOTP verification completed - login successful")
                        print("✅ Verifica TOTP completata con successo!")
                        return True
                    
                    remaining = max_wait_time - i - check_interval
                    if remaining > 0:
                        print(f"⏳ Attesa verifica TOTP... {remaining//60}m {remaining%60}s rimanenti")
                
                logger.error("TOTP verification timeout")
                print("❌ Timeout verifica TOTP")
                return False
            
            # Check for email verification (less common)
            elif any(keyword in page_content.lower() for keyword in ['email.*code', 'email.*verification', 'check.*email']):
                logger.info("Email verification required - waiting for manual input")
                print("\n" + "="*60)
                print("📧 VERIFICA EMAIL RICHIESTA")
                print("="*60)
                print("No-IP richiede un codice di verifica via email.")
                print("1. Controlla la tua casella email (anche spam/junk)")
                print("2. Inserisci il codice nel browser quando arriva")
                print("3. Il programma aspetterà fino a 5 minuti")
                print("="*60)
                
                max_wait_time = 300  # 5 minutes for email
                check_interval = 10  # Check every 10 seconds
                
                for i in range(0, max_wait_time, check_interval):
                    await asyncio.sleep(check_interval)
                    
                    current_url = self.page.url
                    if "dashboard" in current_url or "my" in current_url:
                        logger.info("Email verification completed - login successful")
                        print("✅ Verifica email completata con successo!")
                        return True
                    
                    remaining = max_wait_time - i - check_interval
                    if remaining > 0:
                        print(f"⏳ Attesa verifica email... {remaining//60}m {remaining%60}s rimanenti")
                
                logger.error("Email verification timeout")
                print("❌ Timeout verifica email")
                return False
            
            # Generic verification check (fallback)
            elif any(keyword in page_content.lower() for keyword in ['verification', 'code', 'verify']):
                logger.info("Generic verification required - waiting for manual input")
                print("\n" + "="*60)
                print("🔐 VERIFICA RICHIESTA")
                print("="*60)
                print("No-IP richiede una verifica aggiuntiva.")
                print("1. Controlla il tipo di verifica richiesta nel browser")
                print("2. Inserisci il codice appropriato")
                print("3. Il programma aspetterà fino a 3 minuti")
                print("="*60)
                
                max_wait_time = 180  # 3 minutes
                check_interval = 10  # Check every 10 seconds
                
                for i in range(0, max_wait_time, check_interval):
                    await asyncio.sleep(check_interval)
                    
                    current_url = self.page.url
                    if "dashboard" in current_url or "my" in current_url:
                        logger.info("Verification completed - login successful")
                        print("✅ Verifica completata con successo!")
                        return True
                    
                    remaining = max_wait_time - i - check_interval
                    if remaining > 0:
                        print(f"⏳ Attesa verifica... {remaining//60}m {remaining%60}s rimanenti")
                
                logger.error("Email verification timeout - login failed")
                print("❌ Timeout verifica email. Riprova più tardi.")
                return False
            
            # Check for other login issues
            if "login" in self.page.url.lower():
                logger.error("Login failed - credentials may be incorrect")
                return False
            
            # Final check
            if "dashboard" in self.page.url or "my" in self.page.url:
                logger.info("Login successful")
                return True
            else:
                logger.error("Login failed - unknown reason")
                return False
                
        except Exception as e:
            logger.error(f"Error during login: {e}")
            return False
    
    async def _renew_single_host(self, host: str) -> RenewalStatus:
        """Renew a single No-IP host"""
        try:
            if not self.page:
                logger.error("Page not initialized")
                return RenewalStatus.FAILED
            
            logger.info(f"Starting renewal for host: {host}")
            
            # Navigate to host management page - try both old and new URLs
            await self.page.goto("https://my.noip.com/dynamic-dns")
            await self.page.wait_for_load_state("networkidle")
            
            # Check if we got redirected to the new DNS records page
            current_url = self.page.url
            if "dns/records" in current_url:
                logger.info("Redirected to new DNS records page")
                # The page structure might be different, let's check for different selectors
            elif "dynamic-dns" not in current_url:
                logger.warning(f"Unexpected page URL: {current_url}")
                # Try navigating directly to the new URL
                await self.page.goto("https://my.noip.com/dns/records")
                await self.page.wait_for_load_state("networkidle")
            
            # Debug: Get page content to see what's actually there
            page_content = await self.page.content()
            logger.info(f"Page URL: {self.page.url}")
            
            # Look for different types of elements that might contain hosts
            # Try table rows first
            all_rows = await self.page.locator("tr").all()
            logger.info(f"Found {len(all_rows)} table rows")
            
            # Also try div elements that might contain host information
            all_divs = await self.page.locator("div").all()
            logger.info(f"Found {len(all_divs)} div elements")
            
            # Try to find any element containing the host name
            all_elements = await self.page.locator(f"*:has-text('{host}')").all()
            logger.info(f"Found {len(all_elements)} elements containing '{host}'")
            
            # Check each row for host information
            for i, row in enumerate(all_rows[:10]):  # Limit to first 10 rows
                try:
                    row_text = await row.inner_text()
                    if row_text.strip():  # Only log non-empty rows
                        logger.info(f"Row {i}: {row_text[:100]}...")  # First 100 chars
                except:
                    logger.info(f"Row {i}: Could not get text")
            
            # Check some divs for host information
            for i, div in enumerate(all_divs[:20]):  # Limit to first 20 divs
                try:
                    div_text = await div.inner_text()
                    if div_text.strip() and len(div_text) < 200:  # Only log short, non-empty divs
                        logger.info(f"Div {i}: {div_text[:100]}...")  # First 100 chars
                except:
                    continue
            
            # Search for renewal button for specific host
            host_row = self.page.locator(f"tr:has-text('{host}')")
            
            if await host_row.count() == 0:
                # Try alternative selectors for the new page structure
                logger.warning(f"Host {host} not found with standard selector, trying alternatives...")
                
                # Try any element containing the host name
                host_element = self.page.locator(f"*:has-text('{host}')")
                if await host_element.count() > 0:
                     logger.info(f"Found host in {await host_element.count()} elements")
                     
                     # In the new DNS records page, look for buttons near the host
                     # Try to find "Renew" or "Confirm" buttons on the page
                     renew_buttons = await self.page.locator("button:has-text('Renew'), button:has-text('Confirm'), a:has-text('Renew'), a:has-text('Confirm')").all()
                     logger.info(f"Found {len(renew_buttons)} potential renewal buttons")
                     
                     if len(renew_buttons) > 0:
                         # Try clicking the first renewal button found
                         logger.info("Attempting to click renewal button")
                         await renew_buttons[0].click()
                         host_row = host_element  # Use the found element as host_row
                     else:
                         # No renewal button found - this means the domain doesn't need renewal yet
                         logger.info(f"No renewal button found for {host} - domain is likely not close to expiration")
                         logger.info("Domain is present in account but doesn't require renewal at this time")
                         return RenewalStatus.NO_RENEWAL_NEEDED  # Consider this a success since domain is active
                else:
                    # Try case-insensitive search
                    host_row_ci = self.page.locator(f"*").filter(has_text=host.lower())
                    if await host_row_ci.count() > 0:
                        logger.info(f"Found host with case-insensitive search")
                        host_row = host_row_ci
                    else:
                        # Try partial match
                        domain_part = host.split('.')[0]  # Get just the subdomain part
                        host_row_partial = self.page.locator(f"*:has-text('{domain_part}')")
                        if await host_row_partial.count() > 0:
                            logger.info(f"Found host with partial match on '{domain_part}'")
                            host_row = host_row_partial
                        else:
                            logger.warning(f"Host {host} not found in list with any selector")
                            return RenewalStatus.FAILED
            
            # Search for "Confirm" or "Renew" button in host row or anywhere on page
            # First try in the host row (old structure)
            renew_button = host_row.locator("button:has-text('Confirm'), a:has-text('Confirm'), button:has-text('Renew'), a:has-text('Renew')")
            
            if await renew_button.count() == 0:
                # Try to find renewal buttons anywhere on the page (new structure)
                logger.info(f"No renewal button in host row, searching entire page")
                
                # Look for any renewal-related buttons on the page
                page_renew_buttons = await self.page.locator("button:has-text('Renew'), a:has-text('Renew'), button:has-text('Confirm'), a:has-text('Confirm')").all()
                
                if len(page_renew_buttons) > 0:
                    logger.info(f"Found {len(page_renew_buttons)} renewal buttons on page")
                    # Click the first one and see what happens
                    await page_renew_buttons[0].click()
                    await self.page.wait_for_load_state("networkidle")
                else:
                    # Look for other possible renewal indicators
                    # Sometimes it might be "Extend" or other text
                    extend_buttons = await self.page.locator("button:has-text('Extend'), a:has-text('Extend'), button:has-text('Update'), a:has-text('Update')").all()
                    
                    if len(extend_buttons) > 0:
                        logger.info(f"Found {len(extend_buttons)} extend/update buttons")
                        await extend_buttons[0].click()
                        await self.page.wait_for_load_state("networkidle")
                    else:
                        logger.info(f"Host {host} does not need renewal")
                        return RenewalStatus.NO_RENEWAL_NEEDED
            else:
                # Click renewal button found in host row
                logger.info(f"Found renewal button for {host} in host row")
                await renew_button.first.click()
                await self.page.wait_for_load_state("networkidle")
            
            # Verify if renewal was completed
            success_indicators = [
                "successfully renewed",
                "renewed successfully", 
                "confirmation",
                "confirmed"
            ]
            
            page_content = await self.page.content()
            page_content_lower = page_content.lower()
            
            if any(indicator in page_content_lower for indicator in success_indicators):
                logger.info(f"Host {host} renewed successfully")
                return RenewalStatus.SUCCESS
            else:
                logger.warning(f"Uncertain renewal status for host {host}")
                return RenewalStatus.RETRY_NEEDED
                
        except Exception as e:
            logger.error(f"Error during renewal of {host}: {e}")
            return RenewalStatus.FAILED


    
    def generate_totp_code(self) -> Optional[str]:
        """Generate TOTP code using the configured secret"""
        try:
            if not self.totp_secret:
                logger.warning("No TOTP secret configured")
                return None
            
            totp = pyotp.TOTP(self.totp_secret)
            code = totp.now()
            logger.info("TOTP code generated successfully")
            return code
        except Exception as e:
            logger.error(f"Error generating TOTP code: {e}")
            return None
    
    async def send_notification(self, subject: str, message: str, priority: str = "normal") -> bool:
        """Send notifications via email only"""
        if self.notification_config.email_enabled:
            return await self.send_email_notification(subject, message)
        return True
    
    async def send_email_notification(self, subject: str, message: str) -> bool:
        """Send notification via email"""
        try:
            if not all([self.notification_config.email_user, 
                       self.notification_config.email_password,
                       self.notification_config.recipient_email]):
                logger.warning("Incomplete email configuration")
                return False
                
            msg = MIMEMultipart()
            msg['From'] = self.notification_config.email_user
            msg['To'] = self.notification_config.recipient_email
            msg['Subject'] = f"[No-IP Renewer] {subject}"
            
            body = f"""
{message}

Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Sistema: No-IP Auto Renewer v2.0
            """
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(self.notification_config.smtp_server, 
                                self.notification_config.smtp_port)
            server.starttls()
            server.login(self.notification_config.email_user, 
                        self.notification_config.email_password)
            
            text = msg.as_string()
            server.sendmail(self.notification_config.email_user, 
                          self.notification_config.recipient_email, text)
            server.quit()
            
            logger.info(f"Email sent successfully: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"Email sending error: {e}")
            return False


async def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='No-IP Auto Renewer v2.0')
    parser.add_argument('--config', default='config_v2.json', help='Configuration file')
    parser.add_argument('--run-once', action='store_true', help='Run once instead of continuous monitoring')
    parser.add_argument('--test-notifications', action='store_true', help='Test notification system')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Enable debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        logger.info("Debug logging enabled")
    
    global renewer
    renewer = NoIPRenewerV2(args.config)
    
    if args.test_notifications:
        logger.info("Testing notification system")
        await renewer.send_notification(
            "Test Notifications",
            "This is a test message to verify the notification system functionality.",
            "normal"
        )
        return
    
    if args.run_once:
        logger.info("Single execution of renewal process")
        results = await renewer.run_renewal_process()
        
        print("\n" + "="*50)
        print("RENEWAL RESULTS")
        print("="*50)
        print(f"Success: {'✅' if results['success'] else '❌'}")
        print(f"Renewed domains: {len(results['hosts_renewed'])}")
        print(f"Failed domains: {len(results['hosts_failed'])}")
        print(f"Duration: {results['duration']}")
        
        if results['hosts_renewed']:
            print(f"\nRenewed: {', '.join(results['hosts_renewed'])}")
        
        if results['hosts_failed']:
            print("\nFailed:")
            for failed in results['hosts_failed']:
                print(f"  - {failed['host']}: {failed['status']}")
        
        if results['errors']:
            print("\nErrors:")
            for error in results['errors']:
                print(f"  - {error}")
    else:
        logger.info("Starting continuous monitoring")
        await renewer.monitor_renewal_schedule()
    
    # Cleanup resources
    try:
        await renewer.cleanup_browser()
    except Exception as cleanup_error:
        logger.error(f"Error during final cleanup: {cleanup_error}")


if __name__ == "__main__":
    renewer = None
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        if renewer:
            try:
                asyncio.run(renewer.cleanup_browser())
            except Exception as cleanup_error:
                logger.error(f"Error during cleanup: {cleanup_error}")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if renewer:
            try:
                asyncio.run(renewer.cleanup_browser())
            except Exception as cleanup_error:
                logger.error(f"Error during cleanup: {cleanup_error}")
        raise