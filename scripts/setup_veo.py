#!/usr/bin/env python3
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def setup_veo_login():
    """Manually set up Google Veo login"""
    options = webdriver.ChromeOptions()
    options.binary_location = "/usr/bin/chromium"
    
    # Create user data directory for persistent login
    user_data_dir = os.path.expanduser("~/FantasyAI/chrome_profile")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    
    # Don't run headless for setup
    # options.add_argument("--headless")  # Commented out for manual login
    
    driver = webdriver.Chrome(
        service=Service("/usr/bin/chromedriver"),
        options=options
    )
    
    try:
        print("🌐 Opening Google Veo for manual login...")
        print("Please log in with your Google account when prompted")
        print("After logging in, you can close the browser")
        
        driver.get("https://veo.google/")
        
        # Wait for user to complete login
        input("Press Enter after you've completed the login process...")
        
        print("✅ Google Veo login setup complete!")
        
    finally:
        driver.quit()

if __name__ == "__main__":
    setup_veo_login()
