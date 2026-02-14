import pyautogui
import time

print("Move mouse to target positions within 5 seconds...")
time.sleep(5)

print("\n=== CAPCUT COORDINATES ===")
print(f"Import Button: {pyautogui.position()}")
pyautogui.click()

print("Click Apply Template button position...")
time.sleep(2)
print(f"Apply Button: {pyautogui.position()}")
pyautogui.click()

print("Click Export button position...")
time.sleep(2)
print(f"Export Button: {pyautogui.position()}")

print("\nUpdate these in pipeline.py edit_with_capcut() function")
