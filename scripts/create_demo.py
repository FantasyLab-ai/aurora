#!/usr/bin/env python3
import subprocess
import time
import os

def run_command(cmd, description):
    print(f"\n\033[1;34m=== {description} ===\033[0m")
    print(f"\033[0;33m$ {cmd}\033[0m")
    subprocess.run(cmd, shell=True, text=True)
    time.sleep(1)

def main():
    print("\033[1;32m🎬 FantasyAI Pipeline Demo\033[0m")
    print("Creating end-to-end demonstration...")
    
    # Show project structure
    run_command("ls -la", "Project Structure")
    run_command("ls -la scripts/", "Scripts Directory")
    
    # Show trending analysis
    run_command("cd ~/FantasyAI && python scripts/trend_cluster.py", "Trend Analysis")
    
    # Show prompt generation
    run_command("cd ~/FantasyAI && python scripts/prompt_generator.py --test", "Prompt Generation")
    
    # Show pipeline (simulated)
    print("\n\033[1;34m=== Video Generation ===\033[0m")
    print("\033[0;33m(Simulating Google Veo video generation)\033[0m")
    time.sleep(2)
    
    print("\n\033[1;34m=== Video Editing ===\033[0m")
    print("\033[0;33m(Simulating CapCut automated editing)\033[0m")
    time.sleep(2)
    
    print("\n\033[1;34m=== Social Media Upload ===\033[0m")
    print("\033[0;33m(Simulating TikTok/YouTube upload)\033[0m")
    time.sleep(2)
    
    print("\n\033[1;32m✅ Demo completed successfully!\033[0m")
    print("The pipeline automatically processes trends, generates videos, edits them, and uploads to social media.")

if __name__ == "__main__":
    main()
