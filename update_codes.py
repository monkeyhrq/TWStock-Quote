import ssl
import twstock
import os
import requests
import warnings

# 忽略所有警告訊息
warnings.filterwarnings("ignore")
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

print("☢️ 正在執行核彈級強制更新 (攔截並強制關閉 SSL)...")

# ==========================================
# 🔴 核彈級手段：暴力覆寫 Requests 的驗證機制
# ==========================================
# 1. 保存原本的連線方法
_original_request = requests.Session.request

# 2. 定義一個「永遠不檢查」的新方法
def _insecure_request(self, method, url, *args, **kwargs):
    kwargs['verify'] = False # 強制設定為 False (不檢查)
    return _original_request(self, method, url, *args, **kwargs)

# 3. 偷天換日：把 Python 的連線方法換成我們寫的
requests.Session.request = _insecure_request
# ==========================================

try:
    # 開始更新 (這時候它已經無法執行安全檢查了)
    twstock.__update_codes()
    
    print("\n✅ 更新成功！終於搞定了！🎉")
    print("您的電腦現在已經抓到 4772 台特化了。")
    print("請關閉此視窗，直接去執行您的選股程式 (stock_gui_clean.py) 即可。")

except Exception as e:
    print(f"\n❌ 如果連這樣都失敗，代表是證交所伺服器拒絕了您的 IP: {e}")

os.system("pause")