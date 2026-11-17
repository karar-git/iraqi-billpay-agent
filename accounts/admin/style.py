"""The admin page's only stylesheet (inline, no external assets): Neuronyx stone colours, light and dark,
mobile first (tables become cards under 640 px), tabular numbers for money."""

CSS = """
:root{--paper:#FAF9F7;--s100:#F5F5F4;--s200:#E7E5E4;--s300:#D6D3D1;--s500:#78716C;--ink:#1C1917;
--clay:#B4532A;--clay-text:#A84C26;--ok:#3F6212;--bad:#B91C1C;--card:#FFFFFF}
@media (prefers-color-scheme:dark){:root{--paper:#1C1917;--s100:#292524;--s200:#44403C;--s300:#57534E;
--s500:#A8A29E;--ink:#F5F5F4;--clay:#E08A5F;--clay-text:#E08A5F;--ok:#A3E635;--bad:#F87171;--card:#231F1D}
:root .banner,:root button:not(.ghost){color:#1C1917}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,
"Noto Sans Arabic",Tahoma,sans-serif}
a{color:var(--clay-text)}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:12px 16px;background:var(--s100);
border-bottom:1px solid var(--s200)}
header b{font-size:16px;margin-right:8px}
header nav{display:flex;flex-wrap:wrap;gap:4px 12px}
header nav a{text-decoration:none;color:var(--ink);padding:2px 0}
header nav a.on{color:var(--clay-text);border-bottom:2px solid var(--clay)}
.banner{background:var(--clay);color:#fff;padding:8px 16px;font-size:13px}
main{padding:16px;max-width:1180px;margin:0 auto}
h1{font-size:20px;margin:4px 0 12px}h2{font-size:16px;margin:24px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--s200);border-radius:10px;padding:10px 12px}
.tile .k{color:var(--s500);font-size:12px}.tile .v{font-size:20px;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--s200);border-radius:10px;
overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--s200);vertical-align:top}
th{background:var(--s100);font-size:12px;color:var(--s500);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.muted{color:var(--s500)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.pill{display:inline-block;padding:0 8px;border-radius:99px;background:var(--s200);font-size:12px}
.pill.active{background:#DCFCE7;color:#14532D}.pill.frozen,.pill.blocked{background:#FEE2E2;color:#7F1D1D}
.pill.invited,.pill.unset{background:#FEF3C7;color:#78350F}
form.inline{display:inline}
form.box{background:var(--card);border:1px solid var(--s200);border-radius:10px;padding:12px;margin:8px 0;
display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end}
label{display:flex;flex-direction:column;font-size:12px;color:var(--s500);gap:2px}
input,select,textarea{font:inherit;color:var(--ink);background:var(--paper);border:1px solid var(--s300);
border-radius:8px;padding:6px 8px;min-width:0}
label input,label select,label textarea{font-size:16px;color:var(--ink)}
button{font:inherit;border:0;border-radius:8px;padding:7px 14px;background:var(--clay);color:#fff;cursor:pointer}
button.ghost{background:var(--s200);color:var(--ink)}
.flash{padding:8px 12px;border-radius:8px;background:var(--s100);border:1px solid var(--s200);margin:8px 0}
.flash.bad{border-color:var(--bad)}
.cols{display:grid;grid-template-columns:1fr;gap:16px}
@media (min-width:900px){.cols{grid-template-columns:1fr 1fr}}
@media (max-width:640px){
 table.rt thead{display:none}
 table.rt,table.rt tbody,table.rt tr,table.rt td{display:block;width:100%}
 table.rt tr{border-bottom:1px solid var(--s200);padding:6px 0}
 table.rt td{border:0;padding:2px 10px}
 table.rt td::before{content:attr(data-label);color:var(--s500);font-size:12px;margin-right:6px}
 table.rt.kv td.k{color:var(--s500);font-size:12px;padding-top:6px}
 td.n{text-align:left}
}
"""
