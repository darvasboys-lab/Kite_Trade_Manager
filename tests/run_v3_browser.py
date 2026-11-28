"""Offline Chromium checks for V3 cards, compact view and modal details."""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("v3-layout-artifacts")
OUT.mkdir(exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in (320, 360, 390, 430):
            page = browser.new_page(viewport={"width":width,"height":740}, is_mobile=True, has_touch=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:8503/")
            card = page.locator(".minimalCard").first
            card.wait_for()
            assert card.get_attribute("data-details") == "TCS"
            for theme in ("light", "dark"):
                if page.locator("html").get_attribute("data-theme") != theme:
                    page.locator("#theme").click()
                card.click()
                sheet = page.locator("#stockSheet")
                assert sheet.is_visible()
                assert sheet.locator("#sheetTitle").inner_text() == "TCS"
                assert page.locator(".minimalCard").count() == 2
                assert sheet.get_by_text("₹1,052.50", exact=True).count() == 0
                assert sheet.get_by_text("₹1,052.5", exact=True).is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.evaluate("""() => {
                    const el = document.querySelector('#stockSheet');
                    const b=el.getBoundingClientRect();
                    return b.x>=0 && b.right<=innerWidth+1 && b.bottom<=innerHeight+1;
                }""")
                assert page.evaluate("""() => {
                    const rgb=s=>s.match(/[\\d.]+/g).slice(0,3).map(Number);
                    const lum=s=>rgb(s).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((a,n,i)=>a+n*[.2126,.7152,.0722][i],0);
                    const contrast=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
                    return ['#stockSheet .line span','#stockSheet .line b','#stockSheet .primaryAction','.minimalCard .tile strong','.minimalCard .tile.atm strong'].every(s=>{
                        const e=document.querySelector(s);let b=e;
                        while(b && getComputedStyle(b).backgroundColor==='rgba(0, 0, 0, 0)')b=b.parentElement;
                        return b && contrast(getComputedStyle(e).color,getComputedStyle(b).backgroundColor)>=4.5;
                    })
                }""")
                page.screenshot(path=str(OUT / f"{width}-{theme}-sheet.png"))
                page.locator("#closeSheet").click()
                assert not sheet.is_visible()
                page.locator("#layout").click()
                assert page.locator(".compactRow").count()==2
                page.locator(".compactRow").first.click()
                assert sheet.is_visible()
                page.locator("#closeSheet").click()
                page.locator("#layout").click()
                page.locator("#search").fill("SAIL")
                assert page.locator(".minimalCard").count()==1
                page.locator("#search").fill("")
                page.screenshot(path=str(OUT / f"{width}-{theme}-cards.png"))
            card.click()
            page.locator("#sheetContent").get_by_role("button", name="Covered call").click()
            page.locator("#payoff svg").wait_for()
            assert not errors, errors
            page.close()
        browser.close()


if __name__=="__main__":
    main()
