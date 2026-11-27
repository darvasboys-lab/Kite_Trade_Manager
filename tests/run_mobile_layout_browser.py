"""Run with Streamlit fixture server on port 8502 and Playwright Chromium installed."""
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8502/"
OUT = Path("mobile-layout-artifacts")
OUT.mkdir(exist_ok=True)
CHECK = r"""() => {
 const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e);
   return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
 const bounds = e => e.getBoundingClientRect();
 const issues=[];
 const main=document.querySelector('[data-testid="stMain"]');
 const dock=document.querySelector('.st-key-ccs_ios_dock');
 const w=document.documentElement.clientWidth;
 if (!dock || !visible(dock)) issues.push('missing navigation dock');
 if (main && main.scrollWidth>main.clientWidth+3) issues.push('main horizontal overflow');
 if (document.documentElement.scrollWidth>w+3) issues.push('page horizontal overflow');
 if (dock) {
   const buttons=[...dock.querySelectorAll('button')].filter(visible);
   if (buttons.length!==5) issues.push('expected five navigation buttons');
   for (const e of buttons) {
     const r=bounds(e);
     if (r.left< -2 || r.right>w+2 || r.height<44) issues.push('dock button outside viewport or too small');
   }
   for (let i=1;i<buttons.length;i++) if(bounds(buttons[i-1]).right>bounds(buttons[i]).left+2)
     issues.push('dock buttons overlap');
 }
 for(const parent of document.querySelectorAll('.block-container [data-testid="stVerticalBlock"]')) {
   if(parent.closest('.st-key-ccs_ios_dock')) continue;
   const children=[...parent.children].filter(e=>visible(e)&&
     !e.matches('[data-testid="stEmpty"]')&&!e.querySelector('.st-key-ccs_ios_dock')&&
     !e.matches('.st-key-ccs_ios_dock')&&getComputedStyle(e).position!=='fixed');
   for(let i=1;i<children.length;i++){
     const a=bounds(children[i-1]),b=bounds(children[i]);
     if(a.bottom>b.top+3 && a.right>b.left+2 && b.right>a.left+2)
       issues.push('vertical siblings overlap: '+children[i-1].innerText.slice(0,55)+' / '+children[i].innerText.slice(0,55));
   }
 }
 for(const e of document.querySelectorAll('.block-container .ccs-ios-summary,.block-container .ccs-ios-stockhead,.block-container .ccs-card-grid,.block-container [data-testid="stForm"],.block-container [data-testid="stPlotlyChart"],.block-container [data-testid="stDataFrame"]')){
   if(!visible(e))continue;
   const r=bounds(e);
   if(r.left< -2||r.right>w+2)issues.push('content outside viewport: '+(e.className||e.dataset.testid));
 }
 const tiles=[...document.querySelectorAll('[class*="st-key-ccs_ios_tiles_"] [data-testid="stPopover"] button')].filter(visible);
 for(const e of tiles){
   const r=bounds(e);
   if(r.height<44)issues.push('premium tile smaller than touch target');
   for(const p of e.querySelectorAll('p')){
     if(!visible(p))continue;
     const b=bounds(p);
     if(b.left<r.left-2||b.right>r.right+2||b.bottom>r.bottom+2)
       issues.push('premium text overflows its tile');
   }
 }
 if(dock && main){
   const container=document.querySelector('.block-container');
   if(container && parseFloat(getComputedStyle(container).paddingBottom)<bounds(dock).height)
     issues.push('insufficient space reserved for dock');
 }
 return issues;
}"""

def verify(page, name):
    page.wait_for_timeout(500)
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(OUT / (name + ".png")))
    errors = page.locator('[data-testid="stException"]')
    assert not errors.count(), f"{name}: {errors.all_text_contents()}"
    issues = page.evaluate(CHECK)
    assert not issues, f"{name}: {issues}"
    print("PASS", name, flush=True)


THEME_CHECK = r"""() => {
 const rgb = s => (s.match(/[\d.]+/g)||[]).map(Number);
 const lum = rgb => rgb.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
    .reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
 const contrast = (a,b) => (Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
 const visible = e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
 const bg = e=>{
   for(let n=e;n;n=n.parentElement){
     const c=rgb(getComputedStyle(n).backgroundColor);
     if(c.length===3 || (c.length===4 && c[3]>=.99))return c;
   }
   return rgb(getComputedStyle(document.querySelector('.stApp')).backgroundColor);
 };
 const root=getComputedStyle(document.documentElement);
 const expected=rgb(root.getPropertyValue('--surface').trim().startsWith('#')
   ? getComputedStyle(document.querySelector('.st-key-ccs_ios_dock')).backgroundColor : root.getPropertyValue('--surface'));
 const issues=[];
 const surfaces=document.querySelectorAll('[data-testid="stExpander"] details,[data-testid="stExpanderDetails"],[data-testid="stPopoverBody"],[role="listbox"]');
 for(const e of surfaces){
   if(!visible(e))continue;
   const actual=rgb(getComputedStyle(e).backgroundColor);
   if(actual.slice(0,3).join(',')!==expected.slice(0,3).join(','))
     issues.push('native surface does not match CCS theme: '+e.dataset.testid);
 }
 const leaves=document.querySelectorAll('[data-testid="stExpander"] p,[data-testid="stExpander"] pre,[data-testid="stPopoverBody"] p,[role="option"] span,[data-testid="stWidgetLabel"] p');
 for(const e of leaves){
   if(!visible(e)||!e.textContent.trim()||e.closest('.ccs-ios-summary')||e.closest('button:disabled'))continue;
   const fg=rgb(getComputedStyle(e).color),back=bg(e);
   if(fg.length>=3 && back.length>=3 && contrast(fg,back)<4.5)
      issues.push('low contrast: '+e.textContent.trim().slice(0,50));
 }
 return issues;
}"""

def verify_theme(page, name):
    verify(page, name)
    issues=page.evaluate(THEME_CHECK)
    assert not issues, f"{name}: {issues}"


def navigate(page, index, marker):
    page.locator(".st-key-ccs_ios_dock button").nth(index).click()
    page.get_by_text(marker, exact=True).first.wait_for(timeout=30000)

def open_case(browser, width, height, mode):
    context = browser.new_context(viewport={"width":width,"height":height},
        device_scale_factor=3, is_mobile=True, has_touch=True,
        user_agent="Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36")
    page = context.new_page()
    page.goto(BASE+"?layout_case="+mode,wait_until="domcontentloaded")
    page.locator(".st-key-ccs_ios_dock button").first.wait_for(timeout=45000)
    return context,page

def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=["--no-sandbox"])
        for w,h in ((320,700),(360,800),(390,844),(430,932),(740,360)):
            context,page=open_case(browser,w,h,"accepted")
            prefix=f"{w}x{h}"
            page.locator(".ccs-ios-summary").wait_for()
            verify(page,prefix+"-home")
            page.get_by_text("Filters · change scan settings", exact=True).click()
            page.get_by_text("Maximum contract size", exact=True).wait_for()
            verify_theme(page,prefix+"-filters-expanded")
            page.get_by_text("Filters · change scan settings", exact=True).click()
            page.get_by_text("View details", exact=True).first.click()
            verify_theme(page,prefix+"-stock-details-expanded")
            page.get_by_text("View details", exact=True).first.click()
            navigate(page,1,"Market Screener · Shortlisted stocks only")
            verify(page,prefix+"-screener")
            navigate(page,2,"Strategy Builder")
            verify(page,prefix+"-strategy")
            page.get_by_role("button",name="Open strategy builder").click()
            page.get_by_text("Option chain",exact=True).wait_for()
            page.get_by_role("button",name="Apply preset").click()
            page.locator('[data-testid="stPlotlyChart"]').wait_for()
            verify(page,prefix+"-strategy-payoff")
            navigate(page,3,"Profit / loss at expiry")
            verify(page,prefix+"-trades")
            page.get_by_role("tab",name="▦ Buy Stages").click()
            verify(page,prefix+"-buy-stages")
            page.get_by_role("tab",name="☷ Position Details").click()
            verify(page,prefix+"-position-details")
            navigate(page,4,"FYERS · read-only market data")
            verify(page,prefix+"-settings")
            if w==390:
                page.get_by_role("button",name="Dark mode",exact=True).click()
                verify_theme(page,prefix+"-settings-dark")
                navigate(page,0,"Filters · change scan settings")
                page.get_by_text("View details", exact=True).first.click()
                verify_theme(page,prefix+"-dark-details")
                page.get_by_text("View details", exact=True).first.click()
                page.get_by_text("Filters · change scan settings", exact=True).click()
                verify_theme(page,prefix+"-dark-filters")
                page.locator('[data-testid="stSelectbox"]').first.click()
                page.locator('[role="listbox"]').wait_for()
                verify_theme(page,prefix+"-dark-dropdown")
                page.keyboard.press("Escape")
                page.get_by_text("Filters · change scan settings", exact=True).click()
                page.locator('[class*="st-key-ccs_ios_tiles_"] [data-testid="stPopover"] button').first.click()
                page.locator('[data-testid="stPopoverBody"]').wait_for()
                verify_theme(page,prefix+"-dark-premium-popover")
                page.keyboard.press("Escape")
                navigate(page,1,"Market Screener · Shortlisted stocks only")
                verify_theme(page,prefix+"-dark-screener")
                navigate(page,2,"Strategy Builder")
                verify_theme(page,prefix+"-dark-strategy")
                navigate(page,3,"Profit / loss at expiry")
                verify_theme(page,prefix+"-dark-trades")
            context.close()
        for mode in ("locked","loading","ready"):
            context,page=open_case(browser,390,844,mode)
            page.locator(".ccs-ios-summary").wait_for()
            if mode=="loading":
                page.get_by_text("60% · Option chains 78/135",exact=True).wait_for()
                assert page.locator(".ccs-ios-progress").count()==1
            if mode=="ready":
                page.get_by_role("button",name="Update now").wait_for()
            verify(page,"390-"+mode)
            context.close()
        browser.close()

if __name__=="__main__":
    main()
