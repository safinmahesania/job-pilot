# check_banks2.py — aur variations
import httpx
cands = {
    "TD Campus": ("td","wd3","TD_Campus"),
    "TD Students": ("td","wd3","TD_Bank_Campus_Careers"),
    "TD Early": ("td","wd3","Early_Talent"),
    "Scotia Student": ("scotiabank","wd3","Student"),
    "Scotia Campus": ("scotiabank","wd3","Campus"),
    "RBC Campus": ("rbc","wd3","RBCCAMPUS"),
    "National Bank": ("bnc","wd3","Careers"),
}
for name,(t,h,s) in cands.items():
    url=f"https://{t}.{h}.myworkdayjobs.com/wday/cxs/{t}/{s}/jobs"
    try:
        r=httpx.post(url,json={"limit":3,"offset":0,"searchText":""},headers={"Content-Type":"application/json"},timeout=15)
        print(("OK  " if r.status_code==200 else f"{r.status_code} "),name,f"{t}/{h}/{s}", (r.json().get("total","") if r.status_code==200 else ""))
    except Exception as e:
        print("ERR",name,str(e)[:30])