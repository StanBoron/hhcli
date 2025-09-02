# open_browser.py
import webbrowser
import time
import urllib.request

URL = "http://127.0.0.1:8501"

def wait_for_server(url, timeout=60):
    """Ждём, пока сервер не начнёт отвечать"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        print("⏳ Waiting for server...")
        time.sleep(2)
    return False

if __name__ == "__main__":
    if wait_for_server(URL):
        print(f"🌍 Opening {URL} in your default browser")
        webbrowser.open(URL)
    else:
        print(f"❌ Server {URL} did not respond within timeout")
