import httpx

# Bank early-talent / student Workday sites — common patterns test
candidates = {
    "TD Early Talent":   ("td", "wd3", "TD_Bank_Early_Talent_Careers"),
    "CIBC Campus":       ("cibc", "wd3", "campus"),
    "BMO":               ("bmo", "wd3", "External"),
    "BMO Campus":        ("bmo", "wd3", "Campus"),
    "Scotiabank Student":("scotiabank", "wd3", "Scotiabank_Student_Careers"),
}

for name, (tenant, host, site) in candidates.items():
    url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = httpx.post(url, json={"limit": 5, "offset": 0, "searchText": ""},
                       headers={"Content-Type": "application/json", "Accept": "application/json"},
                       timeout=20)
        if r.status_code == 200:
            total = r.json().get("total", "?")
            print(f"OK   {name:22} {tenant}/{host}/{site} -> {total} jobs")
        else:
            print(f"{r.status_code}  {name:22} {tenant}/{host}/{site}")
    except Exception as e:
        print(f"ERR  {name:22} {str(e)[:40]}")