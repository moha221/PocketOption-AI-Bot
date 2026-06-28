تشغيل البوت:

1) افتح PowerShell داخل المجلد.
2) ثبت المكتبات:
   pip install aiohttp
   pip install "https://github.com/ChipaDevTeam/BinaryOptionsTools-v2/releases/download/v0.2.9/binaryoptionstoolsv2-0.2.9-cp39-abi3-win_amd64.whl"

3) شغل:
   python run.py --token "BOT_TOKEN" --chatid "CHAT_ID"

4) الصق SSID عندما يطلبه.

5) من تيليجرام:
   /menu
   ثم استخدم الأزرار.

مهم:
- Max Subscriptions هو عدد الأزواج التي يشترك بها البوت مباشرة. إذا رفعتها كثيرًا قد يظهر Maximum subscriptions limit reached.
- Max Open Trades هو عدد الصفقات المفتوحة بنفس الوقت. اجعله 3 إذا تريد أكثر من زوج بنفس اللحظة.
- التداول يبدأ OFF. اضغط تشغيل التداول من تيليجرام أو أرسل /run.
