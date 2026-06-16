"""Select element and custom dropdown strategies."""
import time


def try_select_tag(el, f, ans):
    if f["tag"] != "SELECT":
        return
    try:
        values = ans if isinstance(ans, list) else [ans]
        selected = [next((o for o in f.get("options", []) if v.lower() == o.lower()), v) for v in values]
        el.select_option(selected if len(selected) > 1 else selected[0])
        return True
    except Exception:
        return False


def try_dropdown(page, f, ans):
    if f["tag"] != "DROPDOWN":
        return
    sel = None
    if f.get("id"): sel = f'[id="{f["id"]}"]'
    elif f.get("data_automation_id"): sel = f'[data-automation-id="{f["data_automation_id"]}"]'
    elif f.get("name"): sel = f'[name="{f["name"]}"]'
    if not sel: return
    try:
        btn = page.locator(sel)
        if btn.count() == 0: return
        btn.first.click(force=True, timeout=5000)
        time.sleep(1)
        opt = page.locator(f'[role="option"]:has-text("{ans}")')
        if opt.count() > 0:
            opt.first.click(force=True, timeout=3000)
            time.sleep(0.5)
            return True
        lb = page.locator(f'[role="listbox"]:has-text("{ans}")')
        if lb.count() > 0:
            lb.first.click(force=True, timeout=3000)
            time.sleep(0.5)
            return True
        btn.first.click(force=True, timeout=5000)
    except Exception:
        pass
    return
