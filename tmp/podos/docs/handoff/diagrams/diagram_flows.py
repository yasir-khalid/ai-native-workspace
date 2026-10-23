from PIL import Image,ImageDraw,ImageFont
from pathlib import Path
P=Path(__file__).parent
FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FB='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def flow(name,title,steps):
 W,H=1400,490
 im=Image.new('RGB',(W,H),'white'); d=ImageDraw.Draw(im)
 title_font=ImageFont.truetype(FB,32)
 badge_font=ImageFont.truetype(FB,19)
 card_font=ImageFont.truetype(FB,22)
 descr_font=ImageFont.truetype(FONT,18)
 d.text((48,24),title,font=title_font,fill='#193b61')
 cw,ch=308,140; x0=35; gap=30; y1=82; y2=302
 def card(idx,x,y,head,sub):
  d.rounded_rectangle([x,y,x+cw,y+ch],radius=21,fill='#eef4fb',outline='#adc6df',width=3)
  d.ellipse([x+14,y+14,x+47,y+47],fill='#2c69a0')
  d.text((x+25,y+17),str(idx),font=badge_font,fill='white')
  d.text((x+60,y+18),head,font=card_font,fill='#183a5a')
  for j,line in enumerate(sub):
   d.text((x+20,y+68+j*29),line,font=descr_font,fill='#3b5368')
 def arrow(x1,y1,x2,y2):
  d.line((x1,y1,x2,y2),fill='#6b8cae',width=5)
  import math
  a=math.atan2(y2-y1,x2-x1)
  length=15
  p1=(x2-length*math.cos(a-.5),y2-length*math.sin(a-.5));p2=(x2-length*math.cos(a+.5),y2-length*math.sin(a+.5))
  d.polygon([p1,p2,(x2,y2)],fill='#6b8cae')
 for k,(head,sub) in enumerate(steps):
  row=k//4; col=k%4
  # snake sequence to preserve arrows: 1->4 left-to-right, 5->8 right-to-left
  x=x0+(col if row==0 else (3-col))*(cw+gap); y=y1 if row==0 else y2
  card(k+1,x,y,head,sub)
 for j in range(3):
  x=x0+j*(cw+gap)
  arrow(x+cw+4,y1+ch/2,x+cw+gap-4,y1+ch/2)
 arrow(x0+3*(cw+gap)+cw/2,y1+ch+5,x0+3*(cw+gap)+cw/2,y2-9)
 for j in range(3):
  x=x0+(3-j)*(cw+gap)
  arrow(x-3,y2+ch/2,x-gap+4,y2+ch/2)
 im.save(P/(name+'.png'))

flow('sync_flow','Interactive request: secure direct / Auto routing',[
 ('Employee',['Choose pod','Submit question']),
 ('Verify',['Session and','pod membership']),
 ('Discover',['Filter entitled','agents and rollout']),
 ('Route',['Direct selection','or ADK Auto']),
 ('Check',['Re-authorise','before dispatch']),
 ('Invoke',['Scoped A2A/ADK','domain execution']),
 ('Stream',['AG-UI / SSE','progress + result']),
 ('Persist',['Private thread','and artifacts'])])
flow('async_flow','Proactive research: background work to actionable inbox',[
 ('Trigger',['Monitor, filing,','or schedule']),
 ('Agent run',['Pod-scoped','background task']),
 ('Ingress',['Authenticate and','deduplicate event']),
 ('Persist',['Work item and','transactional outbox']),
 ('Notify',['Inbox + optional','Teams / email']),
 ('Review',['PM sees sources','and proposed action']),
 ('Validate',['Check approver,','state and version']),
 ('Resume',['Idempotent','workflow action'])])
