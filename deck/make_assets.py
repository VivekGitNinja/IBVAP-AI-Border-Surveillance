#!/usr/bin/env python3
"""SAJJATA deck asset generator — dark defence theme.
Renders 10 slide-sized PNGs (1998x1124 px) into ./assets/."""

import os, math
from PIL import Image, ImageDraw, ImageFont

W, H = 1998, 1124
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
os.makedirs(ASSETS, exist_ok=True)

# palette
BG=(10,16,28); BG2=(13,21,36); PANEL=(17,26,44); PANEL2=(22,33,54); EDGE=(40,56,86)
INK=(233,240,250); SUB=(152,168,194); FAINT=(104,122,150)
CYAN=(77,194,241); BLUE=(77,144,250); AMBER=(246,170,50); RED=(240,92,86)
GREEN=(64,199,129); VIOLET=(160,120,245); DARKCYAN=(18,44,62); DARKRED=(52,24,28); DARKAMB=(54,40,14)

F_AR="/System/Library/Fonts/Supplemental/Arial.ttf"
F_AB="/System/Library/Fonts/Supplemental/Arial Bold.ttf"
F_DEV="/System/Library/Fonts/Devanagari Sangam MN.ttc"   # Devanagari-capable system font
def font(sz, bold=False, dev=False):
    try:
        if dev: return ImageFont.truetype(F_DEV, sz)
        return ImageFont.truetype(F_AB if bold else F_AR, sz)
    except Exception: return ImageFont.load_default()

def blank():
    img = Image.new("RGB",(W,H),BG); d = ImageDraw.Draw(img)
    for y in range(H):                                   # subtle vertical gradient
        t=y/H; c=tuple(int(BG[i]+(BG2[i]-BG[i])*t) for i in range(3))
        d.line([(0,y),(W,y)],fill=c)
    for gx in range(0,W,84): d.line([(gx,0),(gx,H)],fill=(14,22,38))
    for gy in range(0,H,84): d.line([(0,gy),(W,gy)],fill=(14,22,38))
    return img,d

def chrome(d,kicker,title,page):
    d.text((54,42),kicker,font=font(21,True),fill=CYAN)
    d.text((54,74),title,font=font(44,True),fill=INK)
    d.line([(54,138),(W-54,138)],fill=EDGE,width=2)
    d.text((54,H-40),"SIH26249 · Air Power — Predictive Maintenance & Fleet Availability · MoD / Defence Services Staff College",font=font(16),fill=FAINT)
    d.text((W-96,H-40),page,font=font(16,True),fill=SUB)

def rr(d,box,r,fill,outline=None,width=2):
    d.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=width)

def arrow(d,p1,p2,color=CYAN,width=3,head=11):
    d.line([p1,p2],fill=color,width=width)
    ang=math.atan2(p2[1]-p1[1],p2[0]-p1[0])
    a1=(p2[0]-head*math.cos(ang-0.42),p2[1]-head*math.sin(ang-0.42))
    a2=(p2[0]-head*math.cos(ang+0.42),p2[1]-head*math.sin(ang+0.42))
    d.polygon([p2,a1,a2],fill=color)

def chip(d,xy,s,fill,fg,fs=17,bold=True,pad=12,outline=None):
    f=font(fs,bold); w=int(f.getlength(s))+2*pad
    rr(d,(xy[0],xy[1],xy[0]+w,xy[1]+fs+16), (fs+16)//2, fill, outline, 2)
    d.text((xy[0]+pad,xy[1]+8),s,font=f,fill=fg)
    return w

def body(d,box,lines,fs=18,fill=SUB,lh=None,bold_first=False):
    lh = lh or fs+10; y=box[1]
    for i,ln in enumerate(lines):
        f=font(fs,bold=(bold_first and i==0))
        d.text((box[0],y),ln,font=f,fill=fill); y+=lh

def head(d,xy,s,fs=22,fill=INK):
    d.text(xy,s,font=font(fs,True),fill=fill)

# ---------------------------------------------------------------- S1 title
def s1():
    img,d=blank()
    cx,cy=1560,560
    for r in (150,240,330,420):
        d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=EDGE,width=2)
    d.pieslice((cx-420,cy-420,cx+420,cy+420),-28,10,fill=(16,34,50))
    d.pieslice((cx-420,cy-420,cx+420,cy+420),152,178,fill=(14,30,46))
    d.line([(cx-460,cy),(cx+460,cy)],fill=(20,34,56),width=2)
    d.line([(cx,cy-460),(cx,cy+460)],fill=(20,34,56),width=2)
    for (px,py) in ((1420,430),(1610,510),(1530,650),(1700,600),(1480,720)):
        d.ellipse((px-5,py-5,px+5,py+5),fill=CYAN)
    chip(d,(54,120),"SMART INDIA HACKATHON 2026 · PS SIH26249",DARKCYAN,CYAN,20)
    d.text((50,200),"SAJJATA",font=font(168,True),fill=INK)
    try:
        ImageFont.truetype(F_DEV, 30); sub="सज्जता  ·  READINESS"; fdev=font(30,True,dev=True)
    except Exception:
        sub="READINESS"; fdev=font(30,True)
    d.text((58,392),sub,font=fdev,fill=CYAN)
    d.text((54,452),"Air Power — Predictive Maintenance & Fleet Availability",font=font(44,True),fill=INK)
    body(d,(58,530),["An integrated AI + Digital-Twin platform that turns fragmented maintenance",
                     "data into proactive decisions — and decisions into aircraft availability."],fs=28,fill=SUB,lh=40)
    x=58
    for t,c in (("Sense → Predict → Explain → Optimise → Act → Learn",CYAN),):
        x+=chip(d,(x,640),t,DARKCYAN,c,19)+14
    for t,_,c in (("MoD · Defence Services Staff College",PANEL2,SUB),
                ("Category: Software",PANEL2,SUB),
                ("Theme: Transportation & Logistics",PANEL2,SUB)):
        x+=chip(d,(x,704),t,PANEL2,c,18)+12
    d.line([(54,830),(760,830)],fill=EDGE,width=2)
    body(d,(58,860),["Team <TEAM NAME>  ·  <Institute>  ·  <Member 1 · Member 2 · Member 3 · Member 4 · Member 5 · Member 6>"],fs=22,fill=INK)
    body(d,(58,902),["Idea submission · 05 October 2026"],fs=19,fill=FAINT)
    rr(d,(54,970,W-54,1052),14,PANEL,EDGE,2)
    d.text((74,992),"Problem taken verbatim from SIH26249: “Low aircraft availability due to fragmented and largely reactive maintenance",font=font(18),fill=SUB)
    d.text((74,1020),"practices… data from health-monitoring systems, technical records, spares and maintenance agencies is not adequately integrated.”",font=font(18),fill=SUB)
    img.save(f"{ASSETS}/s01.png")

# ---------------------------------------------------------------- S2 problem
def s2():
    img,d=blank(); chrome(d,"THE PROBLEM","Reactive maintenance is a data-integration failure first","02 · Problem")
    head(d,(54,170),"TODAY: FOUR SILOS, NO COMMON PICTURE",20,INK)
    silos=[("AIRCRAFT HEALTH MONITORING",["Sensor trends, exceedances,","flight-data signals"],CYAN),
           ("TECHNICAL RECORDS",["Logbooks, defect/incident reports,","modifications, inspections"],BLUE),
           ("SPARES & INVENTORY",["Stock, reservations,","lead times, shelf life"],VIOLET),
           ("MAINTENANCE AGENCIES",["Work orders, schedules,","manpower, bay capacity"],AMBER)]
    y=212
    for t,l,c in silos:
        rr(d,(54,y,560,y+156),14,PANEL,EDGE,2)
        d.ellipse((78,y+26,98,y+46),fill=c)
        d.text((116,y+22),t,font=font(21,True),fill=INK)
        body(d,(116,y+64),l,fs=18)
        arrow(d,(560,y+78),(700,560),EDGE,2,9)
        y+=180
    rr(d,(700,380,1250,760),16,DARKRED,RED,3)
    head(d,(730,412),"RESULT: REACTIVE MAINTENANCE",26,RED)
    body(d,(734,470),["• Faults surface after symptoms — often on the ground",
                      "• Groundings investigated one aircraft at a time",
                      "• Downtime absorbed as ‘normal’",
                      "• Fleet strength known late, managed by exception"],fs=21,fill=INK,lh=42)
    d.text((734,700),"The PS names this exactly: data “is not adequately integrated.”",font=font(17),fill=SUB)
    x=1250; y=200
    conseq=[("Delayed fault prediction",PANEL2,AMBER),("Avoidable aircraft downtime",PANEL2,AMBER),
            ("Sub-optimal utilisation of critical assets",PANEL2,AMBER),("LOW FLEET AVAILABILITY",DARKRED,RED)]
    for t,fill,c in conseq:
        h=118 if "LOW" in t else 96
        rr(d,(x,y,W-54,y+h),14,fill,(RED if "LOW" in t else EDGE),3 if "LOW" in t else 2)
        d.text((x+28,y+30),t,font=font(24 if "LOW" in t else 20,True),fill=c)
        arrow(d,(W//2+ (x-W+54)//2 ,y-16+ (0 if y==200 else 0)),(x+110,y-8),EDGE,2,8) if y>200 else None
        arrow(d,(x+60,y-18),(x+60,y-4),EDGE,2,8) if y>200 else None
        y+=h+42
    arrow(d,(1250,470),(1246,470),RED,3)
    rr(d,(54,820,W-54,960),14,PANEL,EDGE,2)
    head(d,(84,848),"WHAT THE CHAIN COSTS",19,CYAN)
    cols=[("Visibility","No single aircraft-health truth; Excel + paper + silo systems",CYAN),
          ("Prediction","Failures discovered at/by failure, not before it",AMBER),
          ("Planning","Maintenance competes with missions for the same aircraft",VIOLET)]
    x=84
    for t,s,c in cols:
        d.text((x,892),t,font=font(20,True),fill=c); d.text((x,922),s,font=font(16),fill=SUB); x+=620
    d.text((54,992),"No MoD fleet statistics are claimed or used — this slide restates the official PS problem chain.",font=font(15),fill=FAINT)
    img.save(f"{ASSETS}/s02.png")

# ---------------------------------------------------------------- S3 solution
def s3():
    img,d=blank(); chrome(d,"THE SOLUTION","SAJJATA — one integrated chain from data to fleet readiness","03 · Solution")
    steps=[("1","SENSE",["AHM telemetry, flight data,","technical logs"],CYAN),
           ("2","INTEGRATE",["One governed aircraft","data fabric"],BLUE),
           ("3","PREDICT",["Anomaly · failure · RUL","multi-model AI"],VIOLET),
           ("4","EXPLAIN",["What · when · why ·","how confident"],GREEN),
           ("5","OPTIMISE",["Risk, spares, slots,","fleet availability"],AMBER),
           ("6","ACT",["Human-approved","work orders"],RED),
           ("7","LEARN",["Outcomes retrain","the models"],CYAN)]
    x=54; bw=252; gap=22
    for n,t,l,c in steps:
        rr(d,(x,220,x+bw,470),16,PANEL,c,3)
        d.text((x+22,244),n,font=font(40,True),fill=c)
        d.text((x+22,300),t,font=font(27,True),fill=INK)
        body(d,(x+22,346),l,fs=17,lh=26)
        if x+bw+gap < W-54: arrow(d,(x+bw+2,344),(x+bw+gap-4,344),c,3,10)
        x+=bw+gap
    d.text((54,520),"AI RECOMMENDS  ·  HUMAN APPROVES  ·  SYSTEM LEARNS",font=font(24,True),fill=SUB)
    pillars=[("Multi-model AI — not one black box",
              ["Anomaly detection, failure classification, RUL regression and","explainability are separate, auditable models with one contract:","every prediction ships with what/when/why/confidence."]),
             ("Aircraft Digital Twin as the live state",
              ["Each aircraft → subsystem → component → sensor carries health,","risk, RUL, history and recommended action; updated by telemetry,","maintenance events and model outputs."]),
             ("Prediction that ends in a work order",
              ["Risk-ranked recommendations check spares, skills and slots,","route for human approval, and become trackable work orders —","then the twin and the fleet picture update automatically."])]
    x=54; bw=(W-108-2*28)//3
    for i,(t,l) in enumerate(pillars):
        bx=x+i*(bw+28)
        rr(d,(bx,580,bx+bw,880),16,PANEL,EDGE,2)
        d.line([(bx+26,632),(bx+70,632)],fill=CYAN,width=4)
        d.text((bx+26,606),t,font=font(21,True),fill=INK)
        body(d,(bx+26,660),l,fs=17,lh=30)
    rr(d,(54,920,W-54,1040),14,PANEL2,EDGE,2)
    d.text((84,944),"Why this wins: most ‘predictive maintenance’ demos stop at a probability. SAJJATA is judged on the full operational chain —",font=font(19),fill=INK)
    d.text((84,976),"prediction → risk → recommendation → spare check → schedule → human approval → work order → twin & fleet update → learning.",font=font(19),fill=INK)
    img.save(f"{ASSETS}/s03.png")

# ---------------------------------------------------------------- S4 architecture
def s4():
    img,d=blank(); chrome(d,"ARCHITECTURE","Five layers, one security envelope, one MLOps spine","04 · Architecture")
    lx=[54,432,810,1188,1566]; colw=340
    heads=[("DATA SOURCES",CYAN),("SECURE INGESTION",BLUE),("DATA PLATFORM",VIOLET),("INTELLIGENCE",GREEN),("DECISION & ACTION",AMBER)]
    for (hx,c),x in zip(heads,lx):
        chip(d,(x,166),hx,PANEL2,c,18)
    cols=[[("Aircraft health monitoring","sensor & flight data streams"),("Technical & maintenance records","logs, DIs, mods, inspections"),("Spares, ops schedules, agencies","inventory, task orders")],
          [("Streaming collector","buffer · store-and-forward"),("Batch loaders & connectors","scheduled, signed"),("Validation · dedupe · lineage","schema + quality gates")],
          [("Time-series store","telemetry at cycle resolution"),("PostgreSQL","fleet · components · work orders"),("Object lake + feature store","raw data + versioned features")],
          [("Digital Twin service","health · risk · RUL per component"),("Model services","anomaly · failure · RUL · XAI"),("Risk engine","P × S × impact × criticality")],
          [("Maintenance decision engine","recommendation → slot"),("Fleet availability optimiser","plan the whole fleet"),("Spares & procurement + work orders","human approval gate")]]
    ys=224
    for ci,(x,col) in enumerate(zip(lx,cols)):
        y=ys
        for t,s in col:
            rr(d,(x,y,x+colw,y+220),14,PANEL,EDGE,2)
            d.line([(x+24,y+34),(x+52,y+34)],fill=heads[ci][1],width=4)
            body(d,(x+24,y+58),[t],fs=20,fill=INK)
            body(d,(x+24,y+100),[s],fs=17,fill=SUB)
            y+=250
    for i in range(4):
        x1=lx[i]+colw; x2=lx[i+1]
        labels=["telemetry","validated events","features / state","decisions"][i]
        arrow(d,(x1+4,420),(x2-6,420),CYAN,3,10)
        d.text(((x1+x2)//2-40,436),labels,font=font(14),fill=FAINT)
    # security band
    rr(d,(54,960,W-54,1044),14,(16,22,38),RED,2)
    chip(d,(74,980),"SECURITY ENVELOPE — ALL LAYERS",DARKRED,RED,16)
    x=470
    for t in ("Zero-trust access","RBAC · MFA","TLS + AES-256","Immutable audit log","Data classification","Segmented networks","Secrets management","Secure model registry"):
        x+=chip(d,(x,976),t,PANEL,SUB,15)+10
    img.save(f"{ASSETS}/s04.png")

# ---------------------------------------------------------------- S5 AI + twin
def s5():
    img,d=blank(); chrome(d,"AI + DIGITAL TWIN","Multi-model pipeline feeding a living aircraft twin","05 · AI & Twin")
    head(d,(54,168),"MULTI-MODEL PIPELINE (measured on synthetic benchmark)",20,CYAN)
    models=[("A · ANOMALY DETECTION","Isolation Forest on per-unit z-scored sensors (healthy baseline)","flags abnormal sensor regimes early","ROC-AUC 0.92 *",CYAN),
            ("B · FAILURE PREDICTION","Gradient-boosted trees, unit-disjoint validation","“failure within 30 cycles” probability","P 0.87 · R 0.99 · F1 0.93 *",VIOLET),
            ("C · RUL ESTIMATION","Random-forest regression on degradation features","remaining useful cycles + trend","MAE 10.7 cycles *",GREEN),
            ("D · FAILURE-MODE CLASS","component + mode + severity head","routes the recommendation","rule-seeded, expandable",AMBER)]
    y=214
    for t,m,s,k,c in models:
        rr(d,(54,y,920,y+150),14,PANEL,EDGE,2)
        d.text((80,y+22),t,font=font(21,True),fill=c)
        body(d,(80,y+62),[m,s],fs=17,lh=28)
        chip(d,(700,y+92),k,DARKCYAN,INK,17)
        y+=172
    d.text((54,918),"* Measured in-repo on a 24-unit × ~300-cycle synthetic degradation benchmark (unit-disjoint split) — deck/ml_benchmark.py.",font=font(15),fill=FAINT)
    d.text((54,942),"Illustrates methodology only — NOT real fleet performance. Model choices favour interpretability, low data hunger, retrainability.",font=font(15),fill=FAINT)
    arrow(d,(928,520),(1000,520),CYAN,3,12)
    d.text((952,486),"features",font=font(14),fill=FAINT)
    rr(d,(1006,168,1944,1004),16,PANEL,EDGE,3)
    head(d,(1036,192),"AIRCRAFT DIGITAL TWIN — A-017 (demo unit)",22,INK)
    tree=[("AIRCRAFT  A-017",INK,22),("├─ Engine subsystem",SUB,19),("│   ├─ Fuel nozzle  ·  health 71%  ·  RUL 21 cyc  ·  HIGH",AMBER,18),
          ("│   └─ Turbine path  ·  health 88%  ·  RUL 90 cyc  ·  LOW",SUB,18),("├─ Hydraulic subsystem",SUB,19),
          ("│   └─ Pump-A  ·  health 92%  ·  RUL 140 cyc  ·  LOW",SUB,18),("├─ Avionics subsystem",SUB,19),
          ("│   └─ INS unit  ·  health 95%  ·  RUL 200 cyc  ·  LOW",SUB,18),("└─ Landing gear",SUB,19),
          ("    └─ Brake pack  ·  health 84%  ·  RUL 55 cyc  ·  MEDIUM",SUB,18)]
    y=246
    for s,c,fs in tree:
        d.text((1036,y),s,font=font(fs,True if fs>=22 else False),fill=c); y+=34
    rr(d,(1036,570,1914,700),12,PANEL2,EDGE,2)
    d.text((1056,588),"TWIN UPDATES ON",font=font(16,True),fill=CYAN)
    x=1056
    for t in ("telemetry in","maintenance done","component replaced","inspection logged","prediction changed"):
        x+=chip(d,(x,622),t,PANEL,SUB,14)+8
    d.text((1056,672),"→ every update is versioned: the twin is the auditable memory of the aircraft.",font=font(16),fill=SUB)
    rr(d,(1036,724,1914,912),12,PANEL2,EDGE,2)
    d.text((1056,744),"WHY THE TWIN MATTERS",font=font(16,True),fill=CYAN)
    body(d,(1056,780),["• One live state for dashboards, optimiser and audit — not 4 silos",
                       "• Any component’s history: sensor → prediction → action → outcome",
                       "• Fleet view is just the union of aircraft twins",
                       "• New aircraft types = new twin template, same services"],fs=17,lh=30,fill=INK)
    img.save(f"{ASSETS}/s05.png")

# ---------------------------------------------------------------- S6 workflow
def s6():
    img,d=blank(); chrome(d,"CLOSED-LOOP WORKFLOW","Every prediction becomes a tracked, human-approved action","06 · Workflow")
    steps=["Telemetry in","Validated events","Health monitor","Anomaly","Failure prediction","RUL","Risk score","Explanation (XAI)","Recommendation","Spare check","Slot optimised","HUMAN APPROVAL","Work order","Maintenance done","Twin update","Fleet availability + ML feedback"]
    x,y=54,250; bw=225; gap=20; rowh=190
    for i,s in enumerate(steps):
        hot = s=="HUMAN APPROVAL"
        c = AMBER if hot else CYAN
        rr(d,(x,y,x+bw,y+120),14,(DARKAMB if hot else PANEL),c,3 if hot else 2)
        d.text((x+18,y+20),f"{i+1:02d}",font=font(22,True),fill=c)
        body(d,(x+18,y+58),[s],fs=19,fill=INK if hot else INK)
        if i<len(steps)-1:
            nx=x+bw+gap
            if nx+bw<=W-54: arrow(d,(x+bw+2,y+60),(nx-4,y+60),c,3,10)
        if i==7:
            arrow(d,(x+bw//2,y+124),(x+bw//2,y+rowh+60),EDGE,2,9)  # down to next row start
        x+=bw+gap
        if x+bw>W-54 and i<len(steps)-1:
            x=54; y+=rowh
    # connect end of row1 to start of row2 explicitly
    arrow(d,(54+16,250+rowh+128),(54+16,250+rowh+160),EDGE,2,9)
    d.text((90,250+rowh+128),"continues ↓",font=font(15),fill=FAINT)
    ry=250+rowh+170
    rr(d,(54,ry,W-54,ry+220),16,PANEL,EDGE,2)
    mid=(ry+ry+220)//2
    d.line([(150,ry+40),(150,ry+180)],fill=EDGE,width=2)
    boxes=[("AI RECOMMENDS",CYAN,"probability, window, evidence","and a proposed action"),
           ("HUMAN APPROVES",AMBER,"engineer/planner accepts, edits","or rejects — with signature"),
           ("SYSTEM LEARNS",GREEN,"outcome vs prediction logged","→ features & models retrain")]
    x=200
    for t,c,l1,l2 in boxes:
        d.text((x,ry+34),t,font=font(24,True),fill=c)
        body(d,(x,ry+80),[l1,l2],fs=18,lh=28)
        x+=620
    for xx in (560,1180): arrow(d,(xx,mid),(xx+40,mid),EDGE,2,9)
    d.text((54,ry+240),"Loop-back arrow: work-order outcomes and post-maintenance telemetry re-enter step 01 as training data — the chain is a cycle, not a line.",font=font(16),fill=FAINT)
    img.save(f"{ASSETS}/s06.png")

# ---------------------------------------------------------------- S7 fleet + spares
def s7():
    img,d=blank(); chrome(d,"FLEET & SPARES OPTIMISATION","From one prediction to a whole-fleet plan and a ready supply chain","07 · Fleet & Spares")
    head(d,(54,166),"FLEET AVAILABILITY OPTIMISER",21,CYAN)
    ins=["Aircraft health & RUL (twin)","Predicted failures + windows","Mission / training priority","Maintenance capacity & skills","Spare availability","Downtime impact of each slot"]
    y=214
    for s in ins:
        rr(d,(54,y,470,y+70),10,PANEL,EDGE,2); d.text((74,y+22),s,font=font(17),fill=INK); y+=82
    arrow(d,(478,420),(540,420),CYAN,3,11)
    rr(d,(546,240,1010,600),14,PANEL2,GREEN,3)
    d.text((574,264),"OBJECTIVE",font=font(17,True),fill=GREEN)
    body(d,(574,300),["Maximise number & value of","available aircraft, subject to","safety limits and maintenance","constraints.","","Greedy + constraint checks in","prototype; solvers (OR-Tools)","in production."],fs=18,lh=28)
    arrow(d,(1016,420),(1078,420),CYAN,3,11)
    rr(d,(1084,214,1944,604),14,PANEL,EDGE,2)
    d.text((1110,236),"OUTPUT — THIS WEEK’S PLAN (demo)",font=font(17,True),fill=CYAN)
    rows=[("A-017","CRITICAL","fuel-nozzle RUL 21c","slot: today, bay 2","HIGH RISK",RED),
          ("A-009","HIGH","brake pack 55c","slot: Tue, bay 1","MEDIUM",AMBER),
          ("A-005","MEDIUM","INS 200c","slot: Fri, bay 2","LOW",GREEN),
          ("A-021","LOW","healthy","no action","LOW",GREEN)]
    y=280
    for a,st,why,slot,rk,c in rows:
        d.text((1110,y),a,font=font(19,True),fill=INK)
        chip(d,(1190,y-6),st,DARKRED if st in("CRITICAL","HIGH") else PANEL,c,14)
        d.text((1340,y+2),why,font=font(16),fill=SUB)
        d.text((1610,y+2),slot,font=font(16),fill=INK)
        y+=82
    head(d,(54,660),"SPARE OPTIMISATION — prediction drives procurement",21,VIOLET)
    flow=[("Predicted failures","by component"),("Required parts","+ quantities"),("Stock / reserved /","lead time"),("Reserve for","planned window"),("Procure","if shortfall"),("Align schedule","to arrival")]
    x=54
    for i,(t,s) in enumerate(flow):
        rr(d,(x,720,x+270,850),12,PANEL,EDGE,2)
        d.text((x+18,742),t,font=font(18,True),fill=INK); body(d,(x+18,782),[s],fs=15)
        if i<5: arrow(d,(x+274,785),(x+296,785),VIOLET,3,9)
        x+=300
    rr(d,(54,880,1944,1030),12,PANEL2,EDGE,2)
    d.text((80,900),"DEMO — 30-DAY SPARE OUTLOOK",font=font(15,True),fill=VIOLET)
    hdrs=[(80,"PART"),(430,"STOCK"),(580,"RESERVED"),(760,"FORECAST 30d"),(980,"LEAD"),(1120,"STATUS")]
    for hx,h in hdrs: d.text((hx,930),h,font=font(14,True),fill=FAINT)
    rows=[("Fuel nozzle FN-2","6","2","3 (A-017)","12d","OK",GREEN),("Brake pack BP-9","4","1","2","7d","OK",GREEN),("INS cartridge","1","0","1","21d","SHORT — expedite",RED)]
    y=956
    for p,s1,s2,f,l,st,c in rows:
        vals=[p,s1,s2,f,l]
        for (hx,_),v in zip(hdrs,vals): d.text((hx,y),v,font=font(15),fill=INK if v==p else SUB)
        chip(d,(1120,y-8),st,(16,26,20) if c==GREEN else (44,20,22),c,13)
        y+=34
    img.save(f"{ASSETS}/s07.png")

# ---------------------------------------------------------------- S8 product
def s8():
    img,d=blank(); chrome(d,"PRODUCT","Six decision surfaces — each screen ends in an action","08 · Product")
    CM={"GREEN":GREEN,"AMBER":AMBER,"RED":RED,"CYAN":CYAN}
    def frame(box,title):
        x1,y1,x2,y2=box
        rr(d,(x1,y1,x2,y2),12,PANEL,EDGE,2)
        rr(d,(x1,y1,x2,y1+44),12,(24,34,54),None)
        for i,c in enumerate((RED,AMBER,GREEN)):
            d.ellipse((x1+16+i*22,y1+16,x1+28+i*22,y1+28),fill=c)
        d.text((x1+90,y1+12),title,font=font(17,True),fill=SUB)
    frame((54,176,726,560),"FLEET OVERVIEW — commander’s morning picture")
    tiles=[("AVAILABLE","17","GREEN"),("IN MAINTENANCE","4","AMBER"),("AT RISK ≤30d","2","RED"),("ALERTS","3","CYAN")]
    x=78
    for t,v,c in tiles:
        cc=CM[c]
        rr(d,(x,240,x+150,330),10,PANEL2,EDGE,2)
        d.text((x+14,252),t,font=font(13,True),fill=FAINT); d.text((x+14,276),v,font=font(30,True),fill=cc); x+=162
    for i in range(2):
        for j in range(4):
            st=[(GREEN,"A-001"),(GREEN,"A-002"),(AMBER,"A-005"),(GREEN,"A-008"),(RED,"A-017"),(GREEN,"A-019"),(GREEN,"A-021"),(AMBER,"A-009")][(i*4+j)]
            c,nm=st
            rr(d,(78+j*160,352+i*44,78+j*160+140,352+i*44+34),8,(14,22,34),c,2)
            d.text((92+j*160,358+i*44),nm,font=font(14),fill=INK)
    frame((754,176,1426,560),"AIRCRAFT DIGITAL TWIN — one aircraft, full state")
    d.text((778,240),"A-017 · tail plan",font=font(18,True),fill=INK)
    parts=[("Engine · fuel nozzle","71%","RUL 21c",AMBER),("Engine · turbine path","88%","RUL 90c",GREEN),("Hydraulics · pump-A","92%","RUL 140c",GREEN),("Avionics · INS","95%","RUL 200c",GREEN),("Gear · brake pack","84%","RUL 55c",GREEN)]
    y=286
    for p,h,r,c in parts:
        rr(d,(778,y,1402,y+44),8,(14,22,34),EDGE,1)
        d.text((794,y+12),p,font=font(15),fill=INK); d.text((1150,y+12),h,font=font(15,True),fill=c); d.text((1260,y+12),r,font=font(15),fill=SUB)
        rr(d,(1370,y+14,1394,y+30),4,c,None)
        y+=54
    frame((1454,176,1944,560),"PREDICTIVE ALERT — the decision card")
    rr(d,(1478,240,1920,332),10,DARKAMB,AMBER,2)
    d.text((1494,254),"HIGH RISK · fuel nozzle · A-017",font=font(17,True),fill=AMBER)
    body(d,(1478,352),["WHAT  fuel-nozzle clogging trend","WHEN  ~21 cycles (window 16–27)","WHY   EGT margin ↓ · fuel-flow ↑ ·","          vib signature drift (SHAP)","CONF 86% · trend-based, 3 signals","ACTION Replace FN-2 at next","          scheduled ground; spare in stock"],fs=15,lh=25,fill=INK)
    frame((54,600,660,1010),"MAINTENANCE PLANNER — slots, bays, crews")
    for i,(t,_) in enumerate([("MON",0),("TUE",0),("WED",0),("THU",0),("FRI",0)]):
        x=78+i*112
        rr(d,(x,640,x+100,700),8,(14,22,34),EDGE,1); d.text((x+30,654),t,font=font(15,True),fill=SUB)
        rr(d,(x,706,x+100,750),8,DARKAMB,AMBER,2); d.text((x+14,716),"A-009 BP-9",font=font(13),fill=INK)
        if i in (1,4):
            rr(d,(x,760,x+100,804),8,(16,30,44),CYAN,2); d.text((x+14,770),"A-017 FN-2",font=font(13),fill=INK)
    d.text((78,880),"Bays: 2 · crews: 3 · conflict-free plan",font=font(15),fill=FAINT)
    frame((688,600,1294,1010),"SPARE INVENTORY — forecast-aware")
    d.text((712,640),"PART              STOCK   RESERVED   30d FCST   LEAD",font=font(14,True),fill=FAINT)
    rows=[("Fuel nozzle FN-2","6","2","3","12d",GREEN,"OK"),("Brake pack BP-9","4","1","2","7d",GREEN,"OK"),("INS cartridge","1","0","1","21d",RED,"SHORT")]
    y=676
    for p,s1,s2,f,l,c,st in rows:
        d.text((712,y),f"{p:<18}{s1:>5}{s2:>10}{f:>10}{l:>8}",font=font(15),fill=INK)
        chip(d,(1170,y-8),st,(16,26,20) if c==GREEN else (44,20,22),c,12)
        y+=44
    d.text((712,860),"Reservations auto-release if the work order is cancelled.",font=font(13),fill=FAINT)
    frame((1322,600,1944,1010),"ANALYTICS — is the AI any good?")
    d.text((1346,640),"Prediction vs outcome (rolling 90d, demo)",font=font(15,True),fill=SUB)
    pts=[(1380,900),(1420,872),(1460,848),(1500,838),(1540,820),(1580,800),(1620,790),(1660,772),(1700,756),(1740,748)]
    d.line(pts,fill=CYAN,width=4)
    for p in pts: d.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4),fill=CYAN)
    d.line([(1360,930),(1900,930)],fill=EDGE,width=2); d.line([(1360,680),(1360,930)],fill=EDGE,width=2)
    body(d,(1346,952),["alert precision 0.84 · recall 0.90 · drift monitor: green"],fs=14,fill=FAINT)
    img.save(f"{ASSETS}/s08.png")

# ---------------------------------------------------------------- S9 security + feasibility
def s9():
    img,d=blank(); chrome(d,"SECURITY · MLOps · FEASIBILITY","Prototype now, production-ready path, honest about both","09 · Feasibility")
    head(d,(54,164),"SECURITY BY DESIGN",20,RED)
    sec=["Zero-trust access","RBAC — 6 operational roles","MFA on every login","TLS in transit","AES-256 at rest","Signed API tokens","Immutable audit log","Data classification tags","Network segmentation","Secrets management","Secure model registry","Encrypted backups","Anomaly/SIEM monitoring","Degraded-mode lockdown"]
    x,y=54,208
    for i,s in enumerate(sec):
        w=chip(d,(x,y),s,PANEL,SUB,15)
        x+=w+10
        if x>1500: x=54; y+=56
    rr(d,(54,356,1120,470),12,PANEL2,EDGE,2)
    body(d,(74,376),["PROTOTYPE: app-level authn/z, RBAC, TLS, audit logs, container isolation, synthetic data only.",
                     "PRODUCTION: align with MoD security architecture & accreditation (classification, hosting,","key management, personnel controls) before any real data touches the platform."],fs=17,lh=28,fill=INK)
    head(d,(1180,164),"MLOps LOOP",20,GREEN)
    loop=["Data","Train","Validate","Registry","Deploy","Monitor","Drift?","Retrain"]
    x=1180
    for i,s in enumerate(loop):
        chip(d,(x,208),s,(16,26,22),GREEN,14); x+=96
        if i==3: x=1180+40; d.text((1170,244),"→",font=font(18),fill=GREEN)
        elif i<7: d.text((x-24,206),"→",font=font(15),fill=FAINT)
    body(d,(1180,286),["MLflow-style registry · versioned features ·","shadow evaluation · rollback · drift monitors","on inputs & predictions."],fs=16,lh=26)
    head(d,(54,520),"WHAT IS REAL IN THIS SUBMISSION",20,CYAN)
    cols=[("IMPLEMENTED",GREEN,["End-to-end demo pipeline on","synthetic data: ingest → twin →","anomaly/failure/RUL models →","risk → recommendation →","work order → dashboards.","","Benchmark code + metrics","reproducible in repo."]),
          ("PROTOTYPE-READY",AMBER,["Streaming ingestion at scale","connector library for real AHM","exports · solver-based fleet","optimiser · SSO/MFA, HA deploy,","full MLOps automation."]),
          ("PRODUCTION / FUTURE",RED,["Accredited MoD hosting & crypto","integration with official","maintenance & inventory systems","of record · live data governance ·","safety-case & airworthiness","review before any operational use."])]
    x=54; bw=620
    for t,c,items in cols:
        rr(d,(x,560,x+bw,900),14,PANEL,EDGE,2)
        chip(d,(x+22,580),t,(14,24,20) if c==GREEN else ((50,38,14) if c==AMBER else (44,20,22)),c,15)
        body(d,(x+22,632),items,fs=16,lh=26)
        x+=bw+24
    head(d,(54,936),"PHASED PATH",18,INK)
    ph=[("P0 · now","synthetic pilot + benchmark (weeks 1–6)",GREEN),("P1","shadow mode on one unit’s data, no decisions (weeks 7–16)",AMBER),("P2","production integration + accreditation (future)",RED)]
    x=54
    for t,s,c in ph:
        chip(d,(x,972),t,(20,28,44),c,15)
        d.text((x+110+ (0 if x==54 else 0),976),s,font=font(16),fill=SUB)
        x+=660
    img.save(f"{ASSETS}/s09.png")

# ---------------------------------------------------------------- S10 impact
def s10():
    img,d=blank(); chrome(d,"IMPACT","Measured in availability, not vanity metrics","10 · Impact")
    kpis=[("FLEET AVAILABILITY","↑","+3–5 pp","more aircraft mission-ready, same fleet",GREEN),
          ("UNSCHEDULED REMOVALS","↓","−20–30%","fewer surprises, fewer groundings",CYAN),
          ("MAINTENANCE LEAD TIME","↑","≥ 7 days","actions planned before the failure window",BLUE),
          ("MTTR","↓","−15–25%","right part, right slot, first time",VIOLET),
          ("SPARE STOCKOUTS (critical)","↓","→ 0","forecast-driven reservations",AMBER),
          ("ALERT QUALITY","↑","precision ≥ 80%","trust the alert, or fix the model",RED)]
    x,y=54,190; bw=620
    for i,(t,dirt,tgt,s,c) in enumerate(kpis):
        bx=x+(i%3)*(bw+24); by=y+(i//3)*230
        rr(d,(bx,by,bx+bw,by+200),14,PANEL,EDGE,2)
        d.text((bx+24,by+22),t,font=font(19,True),fill=INK)
        chip(d,(bx+24,by+66),f"{dirt} {tgt}",(14,24,20),c,20)
        d.text((bx+24,by+136),s,font=font(16),fill=SUB)
    rr(d,(54,668,W-54,700),4,EDGE,None)
    rr(d,(54,720,W-54,880),16,(15,25,42),CYAN,3)
    d.text((84,760),"FROM REACTIVE MAINTENANCE",font=font(46,True),fill=INK)
    d.text((84,820),"TO PREDICTIVE FLEET READINESS.",font=font(46,True),fill=CYAN)
    d.text((84,878),"“No numerical impact is claimed — these are pilot-evaluation targets with published measurement definitions.”",font=font(16),fill=FAINT)
    head(d,(54,906),"REFERENCES (all public)",16,CYAN)
    refs=["DoDI 4151.22 — Condition-Based Maintenance Plus (esd.whs.mil) · CBM+ Guidebook, DAU 2024 (dau.edu)",
          "USAF RSO-CBM+ — AI for fleet availability (aflcmc.af.mil) · NASA C-MAPSS turbofan degradation benchmark (data.nasa.gov)",
          "NIST SP 800-207 — Zero-Trust Architecture (nist.gov) · SHAP explainability (github.com/shap/shap) · NB: no classified sources used"]
    y=940
    for r in refs:
        d.text((54,y),r,font=font(15),fill=SUB); y+=28
    img.save(f"{ASSETS}/s10.png")

if __name__=="__main__":
    for fn in (s1,s2,s3,s4,s5,s6,s7,s8,s9,s10):
        try:
            fn(); print("ok", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, repr(e))
