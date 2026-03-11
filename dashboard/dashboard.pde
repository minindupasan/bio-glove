// ╔══════════════════════════════════════════════════════════════╗
// ║  BIOSENSOR GLOVE DASHBOARD — Processing 4                   ║
// ║  5 Sensor Modules: Flex x5 | IMU | GSR | HR | Temp          ║
// ╠══════════════════════════════════════════════════════════════╣
// ║  SETUP:                                                       ║
// ║    1. Upload BioGlove.ino to ESP32-S3                        ║
// ║    2. Set COM_PORT below                                      ║
// ║    3. Run this sketch in Processing 4                         ║
// ╠══════════════════════════════════════════════════════════════╣
// ║  GPIO MAPPING                                                 ║
// ║    GPIO1→Ring  GPIO2→Thumb  GPIO3→Middle                     ║
// ║    GPIO4→Pinky  GPIO6→Index                                  ║
// ╚══════════════════════════════════════════════════════════════╝

import processing.serial.*;
import java.io.FileWriter;
import java.io.BufferedWriter;
import java.io.PrintWriter;
import java.io.File;

// !! SET YOUR PORT !!
final String COM_PORT  = "/dev/ttyACM0";  // Linux: /dev/ttyACM0  Windows: COM3  Mac: /dev/tty.usbmodem*
final int    BAUD_RATE = 115200;

// ═══════════════════════════════════════════════════════════════
//  GLOBAL STATE
// ═══════════════════════════════════════════════════════════════
Serial  port;
boolean portOpen = false;
boolean espReady = false;
String  statusMsg = "Waiting for ESP32-S3...";
int     totalPackets = 0;
String  sysGesture = "-";
String  gestDbg = "";   // YES gesture debug: "orient,T_str,I_bent,M_bent,R_bent,roll,pitch"

int     studentId = 1;

int     recFlashTimer = 0;

// ═══════════════════════════════════════════════════════════════
//  FLEX SENSOR DATA
//  Firmware index: [0]=Ring  [1]=Thumb  [2]=Middle  [3]=Pinky  [4]=Index
// ═══════════════════════════════════════════════════════════════
int[]   fxRaw   = {0,0,0,0,0};
int[]   fxKal   = {0,0,0,0,0};
float[] fxPct   = {0,0,0,0,0};
int[]   fxRest  = {2048,2048,2048,2048,2048};
int[]   fxBend  = {1400,1400,1400,1400,1400};
boolean[] fxCal = {false,false,false,false,false};

String[] fxName = {"RING","THUMB","MIDDLE","PINKY","INDEX"};
String[] fxGPIO = {"GPIO1","GPIO2","GPIO3","GPIO4","GPIO6"};

// Display order left→right: Index(4), Middle(2), Ring(0), Pinky(3), Thumb(1)
int[] cardOrder = {4, 2, 0, 3, 1};

// ── Simultaneous calibration — all 5 fingers at once ────────
int   capType  = -1;    // -1=off  0=capturing REST  1=capturing BEND
int   capCount = 0;
int[] capSum   = new int[5];
final int CAP_N = 60;   // ~3 sec at 20Hz FLEX rate

// ═══════════════════════════════════════════════════════════════
//  IMU
// ═══════════════════════════════════════════════════════════════
float imuAx,imuAy,imuAz,imuGx,imuGy,imuGz;
float imuRoll,imuPitch,imuYaw,yawOffset=0;

// ═══════════════════════════════════════════════════════════════
//  GSR
// ═══════════════════════════════════════════════════════════════
int   gsrRaw=0,gsrKal=0,gsrChange=0;
float gsrVoltage=0;
boolean gsrSpike=false;
int   gsrBaseline=2048;

// ═══════════════════════════════════════════════════════════════
//  HR
// ═══════════════════════════════════════════════════════════════
int   hrIR=0,hrRed=0,hrBPM=0,hrSPO2=0,hrLED=0x1F;
boolean hrBeat=false;
int   beatFlashTimer=0,lastBPM=0;

// ═══════════════════════════════════════════════════════════════
//  TEMP
// ═══════════════════════════════════════════════════════════════
float tempC=0,tempF=0;
String tempStatus="--";

// ═══════════════════════════════════════════════════════════════
//  HISTORY
// ═══════════════════════════════════════════════════════════════
final int HIST = 300;
int[][]  hfRaw  = new int[5][HIST];
int[][]  hfKal  = new int[5][HIST];
float[]  hiRoll = new float[HIST];
float[]  hiPitch= new float[HIST];
float[]  hiYaw  = new float[HIST];
int[]    hgRaw  = new int[HIST];
int[]    hgKal  = new int[HIST];
int[]    hBPM   = new int[HIST];
int[]    hSPO2  = new int[HIST];
int[]    hIR    = new int[HIST];
int[]    hRED   = new int[HIST];
int[]    hBeat  = new int[HIST];
float[]  hTemp  = new float[HIST];
int hHead = 0;

// ═══════════════════════════════════════════════════════════════
//  LAYOUT  1550 × 900
// ═══════════════════════════════════════════════════════════════
final int W=1550, H=900;
final int HDR_H=52, BTN_H=44, FTR_H=22, PAD=10;
final int R1_Y = HDR_H + BTN_H + PAD;
final int R1_H = 220;
final int R2_Y = R1_Y + R1_H + PAD;
final int R2_H = 240;
final int IMU_W=480, GSR_W=380, HR_W=340, TEMP_W=316;
final int R3_Y = R2_Y + R2_H + PAD;
final int R3_H = H - R3_Y - FTR_H - PAD;

// ═══════════════════════════════════════════════════════════════
//  COLORS
// ═══════════════════════════════════════════════════════════════
color BG=color(8,10,15), PANEL=color(14,18,26), PANEL2=color(11,14,21);
color BORDER=color(28,36,52), THDIM=color(50,68,95);
color THMID=color(115,140,175), THHI=color(210,225,245);
color RAW_C=color(180,60,60);

// FC[firmware_index]: [0]Ring amber [1]Thumb coral [2]Middle blue [3]Pinky violet [4]Index green
color[] FC = {
  color(255,180,40),   // Ring
  color(255,100,100),  // Thumb
  color(40,160,255),   // Middle
  color(220,80,255),   // Pinky
  color(0,220,140)     // Index
};
color IMU_C=color(0,200,220), GSR_C=color(255,120,40);
color HR_C=color(255,60,80),  SPO_C=color(80,180,255), TMP_C=color(255,210,60);

PFont fBig,fMed,fSm,fTiny,fHuge;

// ═══════════════════════════════════════════════════════════════
//  BUTTONS  {x, y, w, h}
// ═══════════════════════════════════════════════════════════════
int[][] BTNS = {
  {PAD,     HDR_H+8, 150, BTN_H-16},  // 0 SET REST
  {PAD+160, HDR_H+8, 150, BTN_H-16},  // 1 SET BEND
  {PAD+320, HDR_H+8, 120, BTN_H-16},  // 2 CAL GSR
  {PAD+450, HDR_H+8, 120, BTN_H-16},  // 3 ZERO YAW
  {PAD+580, HDR_H+8, 120, BTN_H-16},  // 4 CLEAR HIST
  {PAD+720, HDR_H+8, 60, BTN_H-16},   // 5 ID -
  {PAD+790, HDR_H+8, 60, BTN_H-16},   // 6 ID +
  {PAD+860, HDR_H+8, 120, BTN_H-16},  // 7 REC
};
String[] BTN_LABELS = {"SET REST","SET BEND","CAL GSR","ZERO YAW","CLEAR HIST", "ID -", "ID +", "REC"};
color[]  BTN_COLORS;  // set in setup after GSR_C etc initialised

// ═══════════════════════════════════════════════════════════════
void setup() {
  size(1550, 900);
  frameRate(60);
  fHuge = createFont("Monospaced.bold", 60, true);
  fBig  = createFont("Monospaced.bold", 28, true);
  fMed  = createFont("Monospaced.bold", 14, true);
  fSm   = createFont("Monospaced",      11, true);
  fTiny = createFont("Monospaced",       9, true);
  BTN_COLORS = new color[]{
    color(0,200,120), color(60,140,255), GSR_C, IMU_C, color(120,130,150),
    color(100,110,130), color(100,110,130), color(255,80,80)
  };
  clearHistories();
  try {
    port = new Serial(this, COM_PORT, BAUD_RATE);
    port.bufferUntil('\n');
    portOpen = true;
    statusMsg = "Port open — waiting for READY...";
  } catch (Exception e) {
    portOpen  = false;
    statusMsg = "✖ Port Busy: Close VS Code / PlatformIO Serial Monitor first!";
  }
}

// ═══════════════════════════════════════════════════════════════
void serialEvent(Serial p) {
  String line = trim(p.readStringUntil('\n'));
  if (line == null || line.length() == 0) return;
  if (line.equals("READY")) {
    espReady = true;
    statusMsg = "● LIVE — " + COM_PORT + "  |  Use SET REST / SET BEND buttons to calibrate";
    return;
  }
  totalPackets++;

  try {
    if (line.startsWith("FLEX:")) {
      String[] t = split(line.substring(5), ',');
      if (t.length >= 10) {
        for (int i = 0; i < 5; i++) {
          fxRaw[i] = int(t[i*2]);
          fxKal[i] = int(t[i*2+1]);
          hfRaw[i][hHead] = fxRaw[i];
          hfKal[i][hHead] = fxKal[i];
        }
        if (capType >= 0) {
          for (int i = 0; i < 5; i++) capSum[i] += fxKal[i];
          capCount++;
        }
        
        // Zero-Order Hold Graph Advance (20 Hz)
        int nextH = (hHead + 1) % HIST;
        for (int i=0;i<5;i++) { hfRaw[i][nextH]=hfRaw[i][hHead]; hfKal[i][nextH]=hfKal[i][hHead]; }
        hiRoll[nextH]=hiRoll[hHead]; hiPitch[nextH]=hiPitch[hHead]; hiYaw[nextH]=hiYaw[hHead];
        hgRaw[nextH]=hgRaw[hHead]; hgKal[nextH]=hgKal[hHead];
        hBPM[nextH]=hBPM[hHead]; hSPO2[nextH]=hSPO2[hHead];
        hIR[nextH]=hIR[hHead]; hRED[nextH]=hRED[hHead]; hBeat[nextH]=0; // clear bit automatically
        hTemp[nextH]=hTemp[hHead];
        hHead = nextH;
      }
      return;
    }
    if (line.startsWith("IMU:")) {
      String[] t = split(line.substring(4), ',');
      if (t.length >= 9) {
        imuAx=float(t[0]); imuAy=float(t[1]); imuAz=float(t[2]);
        imuGx=float(t[3]); imuGy=float(t[4]); imuGz=float(t[5]);
        imuRoll=float(t[6]); imuPitch=float(t[7]); imuYaw=float(t[8])-yawOffset;
        hiRoll[hHead]=imuRoll; hiPitch[hHead]=imuPitch; hiYaw[hHead]=imuYaw;
      }
      return;
    }
    if (line.startsWith("GSR:")) {
      String[] t = split(line.substring(4), ',');
      if (t.length >= 5) {
        gsrRaw=int(t[0]); gsrKal=int(t[1]); gsrVoltage=float(t[2]);
        gsrChange=int(t[3]); gsrSpike=t[4].equals("1");
        hgRaw[hHead]=gsrRaw; hgKal[hHead]=gsrKal;
      }
      return;
    }
    if (line.startsWith("HR:")) {
      String[] t = split(line.substring(3), ',');
      if (t.length >= 5) {
        hrIR=int(t[0]); hrRed=int(t[1]); hrBPM=int(t[2]); hrSPO2=int(t[3]);
        hrBeat=t[4].equals("1");
        if (t.length>=6) hrLED=int(t[5]);
        if (hrBeat) beatFlashTimer=millis();
        if (hrBPM>0) lastBPM=hrBPM;
        hBPM[hHead]=hrBPM; hSPO2[hHead]=hrSPO2;
        hIR[hHead]=hrIR; hRED[hHead]=hrRed; hBeat[hHead]=hrBeat?1:0;
      }
      return;
    }
    if (line.startsWith("TEMP:")) {
      String[] t = split(line.substring(5), ',');
      if (t.length >= 3) {
        tempC=float(t[0]); tempF=float(t[1]); tempStatus=t[2];
        hTemp[hHead]=tempC;
      }
      return;
    }
    if (line.startsWith("GESTURE:")) {
      sysGesture = line.substring(8).trim();
      return;
    }
    if (line.startsWith("GESTDBG:")) {
      gestDbg = line.substring(8).trim();
      return;
    }
    if (line.startsWith("CALACK:")) {
      println("ESP32 confirmed: calibration applied");
      statusMsg = "● LIVE — Calibration synced to ESP32";
      return;
    }
  } catch (Exception e) {
    // Silently drop corrupt serial payload packets to keep dashboard alive
    // Console output for debugging: println("Parse Error: " + line);
  }
}

// ═══════════════════════════════════════════════════════════════
void draw() {
  checkFlexCapture();
  smoothFlex();
  background(BG);
  drawBgNoise();
  drawHeader();
  drawButtonBar();
  drawFlexRow();
  drawIMUPanel();
  drawGSRPanel();
  drawHRPanel();
  drawTempPanel();
  drawHistoryRow();
  drawFooter();
}

// ═══════════════════════════════════════════════════════════════
//  CALIBRATION
// ═══════════════════════════════════════════════════════════════
void checkFlexCapture() {
  if (capType < 0 || capCount < CAP_N) return;
  for (int i = 0; i < 5; i++) {
    int avg = capSum[i] / capCount;
    if (capType == 0) fxRest[i] = avg;
    else              fxBend[i] = avg;
    if (abs(fxBend[i] - fxRest[i]) > 30) fxCal[i] = true;
  }
  capType = -1; capCount = 0;
  for (int i = 0; i < 5; i++) capSum[i] = 0;

  // Send calibration to ESP32 so gesture detection uses the same thresholds
  sendCalToESP();
}

void sendCalToESP() {
  if (!portOpen || !espReady) return;
  // Format: SETCAL:rest0,rest1,rest2,rest3,rest4,bend0,bend1,bend2,bend3,bend4
  // Firmware index order: Ring(0), Thumb(1), Middle(2), Pinky(3), Index(4)
  String cmd = "SETCAL:"
    + fxRest[0] + "," + fxRest[1] + "," + fxRest[2] + "," + fxRest[3] + "," + fxRest[4] + ","
    + fxBend[0] + "," + fxBend[1] + "," + fxBend[2] + "," + fxBend[3] + "," + fxBend[4] + "\n";
  port.write(cmd);
  println("Sent to ESP32: " + cmd.trim());
}

void smoothFlex() {
  for (int i = 0; i < 5; i++) {
    float tgt = fxCal[i] ? constrain(map(fxKal[i],fxRest[i],fxBend[i],0,100),0,100) : 0;
    fxPct[i] += (tgt - fxPct[i]) * 0.10;
  }
}

void clearHistories() {
  for (int i = 0; i < 5; i++)
    for (int j = 0; j < HIST; j++) { hfRaw[i][j]=0; hfKal[i][j]=0; }
  for (int j = 0; j < HIST; j++) {
    hiRoll[j]=0; hiPitch[j]=0; hiYaw[j]=0;
    hgRaw[j]=0; hgKal[j]=0;
    hBPM[j]=0; hSPO2[j]=0; hIR[j]=0; hRED[j]=0; hBeat[j]=0; hTemp[j]=0;
  }
  hHead = 0;
}

void drawBgNoise() {
  stroke(255,255,255,4); strokeWeight(1);
  for (int y=0; y<H; y+=4) line(0,y,W,y);
}

// ═══════════════════════════════════════════════════════════════
//  HEADER
// ═══════════════════════════════════════════════════════════════
void drawHeader() {
  noStroke(); fill(PANEL); rect(0,0,W,HDR_H);
  fill(FC[4]); rect(0,0,3,HDR_H);
  fill(THHI); textFont(fMed); textSize(17); textAlign(LEFT,CENTER);
  text("BIOSENSOR GLOVE", 16, HDR_H/2-2);
  fill(THDIM); textSize(10);
  text("FLEX × 5  |  MPU6050  |  GSR  |  MAX30102  |  CJMCU-30205", 16, HDR_H/2+10);

  // GESTURE READOUT + YES debug overlay
  fill(BORDER); noStroke(); rect(320, 10, 300, 32, 5);
  if (!sysGesture.equals("-")) {
    fill(color(0, 220, 140)); textFont(fMed); textSize(16); textAlign(CENTER, CENTER);
    text(sysGesture.toUpperCase(), 320 + 300/2, HDR_H/2);
  } else if (gestDbg.length() > 0) {
    // Show which YES conditions pass/fail: orient,T_str,I_bent,M_bent,R_bent,roll,pitch
    String[] d = split(gestDbg, ',');
    if (d.length >= 7) {
      boolean orient = d[0].equals("1"), Ts = d[1].equals("1");
      boolean Ib = d[2].equals("1"), Mb = d[3].equals("1"), Rb = d[4].equals("1");
      String dbgTxt = (orient?"O":"o") + " " + (Ts?"T":"t") + " " + (Ib?"I":"i") + " " + (Mb?"M":"m") + " " + (Rb?"R":"r")
        + "  R:" + d[5] + " P:" + d[6];
      fill(color(255, 200, 50)); textFont(fTiny); textSize(10); textAlign(CENTER, CENTER);
      text("YES? " + dbgTxt, 320 + 300/2, HDR_H/2);
    }
  } else {
    fill(THDIM); textFont(fSm); textSize(12); textAlign(CENTER, CENTER);
    text("NO GESTURE", 320 + 300/2, HDR_H/2);
  }

  fill(espReady ? color(0,220,130,40) : color(255,100,50,40));
  noStroke(); rect(W-600,10,590,32,5);
  fill(espReady ? color(0,230,140) : color(255,120,60));
  textFont(fTiny); textSize(10); textAlign(RIGHT,CENTER);
  text(statusMsg, W-10, HDR_H/2);
}

// ═══════════════════════════════════════════════════════════════
//  BUTTON BAR
// ═══════════════════════════════════════════════════════════════
void drawButtonBar() {
  noStroke(); fill(PANEL2); rect(0, HDR_H, W, BTN_H);
  stroke(BORDER); strokeWeight(1);
  line(0, HDR_H, W, HDR_H);
  line(0, HDR_H+BTN_H-1, W, HDR_H+BTN_H-1);

  for (int b = 0; b < BTNS.length; b++) {
    boolean active = (b==0 && capType==0) || (b==1 && capType==1);
    int[] btn = BTNS[b];
    String label = BTN_LABELS[b];
    color c = BTN_COLORS[b];
    
    if (b == 7 && millis() - recFlashTimer < 300) {
       label = "SAVED!";
       c = color(255, 255, 255);
       active = true;
    }
    
    drawButton(btn[0], btn[1], btn[2], btn[3], label, c, active);
  }

  fill(THHI); textFont(fMed); textAlign(CENTER, CENTER);
  text("STU ID " + studentId, PAD+1050, HDR_H + BTN_H/2 - 2);

  // Progress bar or hint
  int hinX = PAD+1130;
  if (capType >= 0 && capCount > 0) {
    float prog = constrain(capCount/(float)CAP_N, 0, 1);
    int bw = 290;
    noStroke(); fill(BORDER); rect(hinX, HDR_H+10, bw, BTN_H-20, 3);
    fill(capType==0 ? color(0,200,120) : color(60,140,255));
    rect(hinX, HDR_H+10, (int)(bw*prog), BTN_H-20, 3);
    fill(THHI); textFont(fTiny); textSize(9); textAlign(CENTER,CENTER);
    text((capType==0?"REST":"BEND")+" CAPTURING  "+int(prog*100)+"%", hinX+bw/2, HDR_H+BTN_H/2);
  } else {
    fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,CENTER);
    text("Relax all fingers → SET REST     Fully bend all fingers → SET BEND", hinX, HDR_H+BTN_H/2);
  }
}

void drawButton(int bx, int by, int bw, int bh, String label, color c, boolean active) {
  noStroke();
  if (active) {
    fill(c); rect(bx, by, bw, bh, 4);
    fill(0); textFont(fTiny); textSize(9); textAlign(CENTER,CENTER);
    text(label, bx+bw/2, by+bh/2);
  } else {
    fill(red(c)*0.12, green(c)*0.12, blue(c)*0.12); rect(bx, by, bw, bh, 4);
    stroke(c); strokeWeight(1); noFill(); rect(bx, by, bw, bh, 4); noStroke();
    fill(c); textFont(fTiny); textSize(9); textAlign(CENTER,CENTER);
    text(label, bx+bw/2, by+bh/2);
  }
}

boolean btnHit(int mx, int my, int b) {
  int[] btn = BTNS[b];
  return mx>=btn[0] && mx<=btn[0]+btn[2] && my>=btn[1] && my<=btn[1]+btn[3];
}

void mousePressed() {
  if (btnHit(mouseX, mouseY, 0)) {  // SET REST
    capType=0; capCount=0;
    for (int i=0;i<5;i++) capSum[i]=0;
  }
  if (btnHit(mouseX, mouseY, 1)) {  // SET BEND
    capType=1; capCount=0;
    for (int i=0;i<5;i++) capSum[i]=0;
  }
  if (btnHit(mouseX, mouseY, 2)) gsrBaseline = gsrRaw;   // CAL GSR
  if (btnHit(mouseX, mouseY, 3)) yawOffset += imuYaw;    // ZERO YAW
  if (btnHit(mouseX, mouseY, 4)) clearHistories();       // CLEAR HIST
  if (btnHit(mouseX, mouseY, 5)) {  // ID -
    if (studentId > 1) studentId--;
  }
  if (btnHit(mouseX, mouseY, 6)) {  // ID +
    studentId++;
  }
  if (btnHit(mouseX, mouseY, 7)) {  // REC
    recordSingleDataPoint();
  }
}

void recordSingleDataPoint() {
  String csvFilename = sketchPath("student_" + studentId + ".csv");
  try {
    File f = new File(csvFilename);
    boolean writeHeader = !f.exists() || f.length() == 0;
    FileWriter fw = new FileWriter(f, true);
    BufferedWriter bw = new BufferedWriter(fw);
    PrintWriter csvWriter = new PrintWriter(bw);
    if (writeHeader) {
      csvWriter.println("timestamp,BPM,SPO2,GSR_RAW,GSR_KAL,GSR_VOLTAGE,IR,RED,SKIN_TEMP_C,SKIN_TEMP_F");
    }
    String timestamp = year()+"-"+nf(month(),2)+"-"+nf(day(),2)+" "+nf(hour(),2)+":"+nf(minute(),2)+":"+nf(second(),2)+"."+nf(millis()%1000,3);
    csvWriter.println(timestamp + "," + hrBPM + "," + hrSPO2 + "," + gsrRaw + "," + gsrKal + "," + gsrVoltage + "," + hrIR + "," + hrRed + "," + tempC + "," + tempF);
    csvWriter.flush();
    csvWriter.close();
    recFlashTimer = millis();
    println("Recorded single snapshot for student " + studentId);
  } catch (Exception e) {
    println("Error appending to CSV: " + e.getMessage());
  }
}

// ═══════════════════════════════════════════════════════════════
//  FLEX ROW
// ═══════════════════════════════════════════════════════════════
void drawFlexRow() {
  int fw = (W - 6*PAD) / 5;
  for (int slot = 0; slot < 5; slot++) {
    int si = cardOrder[slot];
    drawFlexCard(si, slot, PAD + slot*(fw+PAD), R1_Y, fw, R1_H);
  }
}

void drawFlexCard(int i, int slot, int cx, int cy, int cw, int ch) {
  color c = FC[i];
  noStroke(); fill(PANEL); rect(cx,cy,cw,ch,6);
  fill(c); rect(cx,cy,cw,3,6,6,0,0);

  fill(c); textFont(fMed); textSize(12); textAlign(LEFT,TOP);
  text(fxName[i], cx+12, cy+10);
  fill(THDIM); textSize(9);
  text(fxGPIO[i], cx+12, cy+24);

  fill(fxCal[i] ? THHI : THDIM);
  textFont(fBig); textSize(44); textAlign(CENTER,TOP);
  text(fxCal[i] ? nf((int)fxPct[i],2)+"%" : "--", cx+cw/2, cy+16);

  drawArc(cx+cw/2, cy+128, 58, fxPct[i], c, fxCal[i]);

  String st; color sc;
  if      (!fxCal[i])   { st="UNCAL";    sc=THDIM; }
  else if (fxPct[i]<15) { st="STRAIGHT"; sc=color(0,220,130); }
  else if (fxPct[i]<65) { st="PARTIAL";  sc=color(255,200,50); }
  else                   { st="BENT";     sc=color(255,70,70); }
  fill(sc); textFont(fTiny); textSize(10); textAlign(CENTER,BOTTOM);
  text(st, cx+cw/2, cy+ch-38);

  fill(RAW_C); textAlign(LEFT,BOTTOM); textSize(9);
  text("R "+fxRaw[i], cx+8, cy+ch-22);
  fill(c);
  text("K "+fxKal[i], cx+8, cy+ch-11);
  fill(THDIM); textAlign(RIGHT,BOTTOM); textSize(9);
  text("REST:"+fxRest[i]+"  BEND:"+fxBend[i], cx+cw-8, cy+ch-11);

  // Capture progress overlay
  if (capType >= 0 && capCount > 0) {
    float prog = constrain(capCount/(float)CAP_N, 0, 1);
    color capC = capType==0 ? color(0,200,120) : color(60,140,255);
    noStroke(); fill(capC, 25); rect(cx,cy,cw,ch,6);
    noStroke(); fill(BORDER); rect(cx+8, cy+ch-8, cw-16, 5, 2);
    fill(capC); rect(cx+8, cy+ch-8, (int)((cw-16)*prog), 5, 2);
  }
}

void drawArc(float cx, float cy, float r, float pct, color c, boolean active) {
  noFill();
  stroke(active ? color(30,40,58) : color(22,30,42));
  strokeWeight(8); strokeCap(SQUARE);
  arc(cx,cy,r*2,r*2,radians(145),radians(395));
  if (active && pct > 0.3) {
    float ea = radians(145) + (pct/100.0)*radians(250);
    stroke(c); strokeWeight(8); strokeCap(ROUND);
    arc(cx,cy,r*2,r*2,radians(145),ea);
    strokeCap(SQUARE);
  }
  noStroke(); fill(active ? c : THDIM); ellipse(cx,cy,6,6);
}

// ═══════════════════════════════════════════════════════════════
//  IMU PANEL
// ═══════════════════════════════════════════════════════════════
void drawIMUPanel() {
  int cx=PAD, cy=R2_Y, cw=IMU_W, ch=R2_H;
  panelBg(cx,cy,cw,ch,IMU_C);
  fill(IMU_C); textFont(fMed); textSize(11); textAlign(LEFT,TOP);
  text("MPU6050  IMU", cx+12, cy+10);
  fill(THDIM); textSize(9); text("SDA→GPIO8  SCL→GPIO9  ADDR=0x68", cx+130, cy+12);
  String[] lbl={"ROLL","PITCH","YAW"}; float[] v={imuRoll,imuPitch,imuYaw};
  for (int i=0;i<3;i++) {
    int bx=cx+10+i*158;
    fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP); text(lbl[i],bx,cy+32);
    fill(IMU_C); textFont(fBig); textSize(26); textAlign(LEFT,TOP); text(nf(v[i],1,1)+"°",bx,cy+44);
  }
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("ACC (g)", cx+12, cy+100); text("GYR (°/s)", cx+12, cy+120);
  fill(THMID);
  text("X:"+nf(imuAx,1,2)+"  Y:"+nf(imuAy,1,2)+"  Z:"+nf(imuAz,1,2), cx+65, cy+100);
  text("X:"+nf(imuGx,1,1)+"  Y:"+nf(imuGy,1,1)+"  Z:"+nf(imuGz,1,1), cx+65, cy+120);
  drawOrientationViz(cx+cw-100, cy+R2_H/2-10, 70);
}

void drawOrientationViz(float cx, float cy, float sz) {
  pushMatrix(); translate(cx,cy);
  float r=radians(imuRoll), p=radians(imuPitch);
  color[] ac={color(255,80,80),color(80,255,80),color(80,130,255)};
  float[][] dirs={{cos(p)*sz,sin(r)*sin(p)*sz-cos(r)*sz*0.5},{0,cos(r)*sz},{sin(p)*sz,-sin(r)*cos(p)*sz}};
  String[] axl={"X","Y","Z"};
  for (int i=0;i<3;i++) {
    stroke(ac[i]); strokeWeight(2); line(0,0,dirs[i][0],dirs[i][1]);
    fill(ac[i]); noStroke(); textFont(fTiny); textSize(9);
    text(axl[i],dirs[i][0]+4,dirs[i][1]+4);
  }
  noFill(); stroke(THDIM); strokeWeight(1); ellipse(0,0,sz*1.8,sz*1.8);
  popMatrix();
}

// ═══════════════════════════════════════════════════════════════
//  GSR PANEL
// ═══════════════════════════════════════════════════════════════
void drawGSRPanel() {
  int cx=PAD+IMU_W+PAD, cy=R2_Y, cw=GSR_W, ch=R2_H;
  panelBg(cx,cy,cw,ch,GSR_C);
  fill(GSR_C); textFont(fMed); textSize(11); textAlign(LEFT,TOP);
  text("GSR  GALVANIC SKIN", cx+12, cy+10);
  fill(THDIM); textSize(9); text("SIG→GPIO5   use CAL GSR button", cx+12, cy+24);
  fill(THHI); textFont(fBig); textSize(38); textAlign(CENTER,TOP);
  text(nf(gsrVoltage,1,3)+"V", cx+cw/2, cy+36);
  float condPct=constrain(map(gsrRaw,800,3000,0,100),0,100);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("CONDUCTANCE", cx+12, cy+108);
  noStroke(); fill(BORDER); rect(cx+12,cy+120,cw-24,14,3);
  fill(gsrSpike ? color(255,60,60) : GSR_C); rect(cx+12,cy+120,(cw-24)*condPct/100,14,3);
  fill(gsrChange<-100 ? color(255,80,80) : gsrChange>100 ? color(80,255,150) : THMID);
  textFont(fSm); textSize(11); textAlign(LEFT,TOP);
  text("Δ baseline: "+(gsrChange>0?"+":"")+gsrChange, cx+12, cy+144);
  fill(THDIM); textSize(9);
  text("RAW:"+gsrRaw+"  KAL:"+gsrKal+"  BASE:"+gsrBaseline, cx+12, cy+162);
  if (gsrSpike) {
    fill(color(255,60,60,200)); noStroke(); rect(cx+cw-80,cy+12,68,22,4);
    fill(255); textFont(fTiny); textSize(10); textAlign(CENTER,CENTER);
    text("⚡ SPIKE", cx+cw-46, cy+23);
  }
  String arousal=condPct<20?"RELAXED":condPct<50?"CALM":condPct<75?"ALERT":"AROUSED";
  color arousalC=condPct<20?color(80,200,255):condPct<50?color(0,220,130):condPct<75?color(255,190,40):color(255,70,70);
  fill(arousalC); textFont(fTiny); textSize(10); textAlign(RIGHT,BOTTOM);
  text(arousal, cx+cw-10, cy+ch-10);
}

// ═══════════════════════════════════════════════════════════════
//  HR PANEL
// ═══════════════════════════════════════════════════════════════
void drawHRPanel() {
  int cx=PAD+IMU_W+PAD+GSR_W+PAD, cy=R2_Y, cw=HR_W, ch=R2_H;
  panelBg(cx,cy,cw,ch,HR_C);
  fill(HR_C); textFont(fMed); textSize(11); textAlign(LEFT,TOP);
  text("MAX30102  HEART RATE", cx+12, cy+10);
  fill(THDIM); textSize(9); text("SDA→GPIO8  SCL→GPIO9", cx+12, cy+24);
  boolean flash=(millis()-beatFlashTimer<200);
  boolean fingerOn=hrIR>8000;
  fill(flash?HR_C:(fingerOn?color(red(HR_C)*0.7,green(HR_C)*0.3,blue(HR_C)*0.3):color(red(HR_C)*0.35,green(HR_C)*0.2,blue(HR_C)*0.2)));
  textFont(fHuge); textSize((int)(50*(flash?1.25:1.0))); textAlign(CENTER,TOP);
  text(lastBPM>0?str(lastBPM):"--", cx+cw/2, cy+32);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(CENTER,TOP); text("BPM", cx+cw/2, cy+88);
  fill(SPO_C); textFont(fBig); textSize(22); textAlign(CENTER,TOP);
  text(hrSPO2>0?str(hrSPO2)+"%":"--%", cx+cw/2, cy+106);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(CENTER,TOP); text("SpO₂", cx+cw/2, cy+132);
  fill(fingerOn?color(0,220,130):color(180,60,60));
  noStroke(); rect(cx+10,cy+150,cw-20,16,4);
  fill(fingerOn?color(0,0,0):THHI); textFont(fTiny); textSize(9); textAlign(CENTER,CENTER);
  text(fingerOn?"FINGER DETECTED":"PLACE FINGER ON SENSOR", cx+cw/2, cy+158);
  float sigPct=constrain(map(hrIR,0,80000,0,100),0,100);
  boolean saturated=hrIR>80000, tooWeak=hrIR>3000&&hrIR<30000;
  color sigC=saturated?color(255,60,60):tooWeak?color(255,200,40):color(0,220,130);
  fill(THDIM); textFont(fTiny); textSize(8); textAlign(LEFT,BOTTOM);
  text("SIGNAL", cx+10, cy+ch-22);
  noStroke(); fill(BORDER); rect(cx+52,cy+ch-30,cw-62,8,2);
  fill(sigC); rect(cx+52,cy+ch-30,(cw-62)*sigPct/100,8,2);
  fill(saturated?color(255,60,60):THDIM); textAlign(RIGHT,BOTTOM);
  text(saturated?"SATURATED":(tooWeak?"WEAK":"OK")+"  LED:0x"+hex(hrLED,2), cx+cw-10, cy+ch-22);
  fill(THDIM); textFont(fTiny); textSize(8); textAlign(LEFT,BOTTOM);
  text("IR:"+hrIR+"  RED:"+hrRed, cx+10, cy+ch-8);
  String hrClass=hrBPM==0?"--":hrBPM<60?"BRADYCARDIA":hrBPM<100?"NORMAL":"TACHYCARDIA";
  color hrCc=hrBPM<60?color(80,160,255):hrBPM<100?color(0,220,130):color(255,70,70);
  fill(hrCc); textFont(fTiny); textSize(9); textAlign(RIGHT,BOTTOM);
  text(hrClass, cx+cw-10, cy+ch-8);
}

// ═══════════════════════════════════════════════════════════════
//  TEMP PANEL
// ═══════════════════════════════════════════════════════════════
void drawTempPanel() {
  int cx=PAD+IMU_W+PAD+GSR_W+PAD+HR_W+PAD, cy=R2_Y, cw=TEMP_W, ch=R2_H;
  panelBg(cx,cy,cw,ch,TMP_C);
  fill(TMP_C); textFont(fMed); textSize(11); textAlign(LEFT,TOP);
  text("CJMCU-30205  SKIN TEMP", cx+12, cy+10);
  fill(THDIM); textSize(9); text("SDA→GPIO8  SCL→GPIO9  ADDR=0x4C", cx+12, cy+24);
  fill(THHI); textFont(fHuge); textSize(44); textAlign(CENTER,TOP);
  text(tempC>0?nf(tempC,2,1)+"°C":"--°C", cx+cw/2, cy+32);
  fill(TMP_C); textFont(fSm); textSize(14); textAlign(CENTER,TOP);
  text(tempF>0?nf(tempF,2,1)+"°F":"--°F", cx+cw/2, cy+90);
  float tPct=constrain(map(tempC,30,38,0,100),0,100);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("30°",cx+12,cy+122); textAlign(RIGHT,TOP); text("38°",cx+cw-12,cy+122);
  noStroke(); fill(BORDER); rect(cx+12,cy+134,cw-24,12,3);
  color tbc=tempC<34?color(80,160,255):tempC<36?TMP_C:color(255,100,60);
  fill(tbc); rect(cx+12,cy+134,(cw-24)*tPct/100,12,3);
  color stC=tempStatus.equals("NORMAL")?color(0,220,130):
            tempStatus.equals("WARM")?color(255,190,40):
            tempStatus.equals("FEVER")?color(255,60,60):
            tempStatus.equals("NO_CONTACT")?color(100,130,180):THDIM;
  fill(stC); noStroke(); rect(cx+cw/2-55,cy+158,110,20,4);
  fill(color(0,0,0,200)); textFont(fTiny); textSize(9); textAlign(CENTER,CENTER);
  text(tempStatus, cx+cw/2, cy+168);
}

// ═══════════════════════════════════════════════════════════════
//  HISTORY ROW
// ═══════════════════════════════════════════════════════════════
void drawHistoryRow() {
  int gy=R3_Y, gh=R3_H;
  int gw1=460,gw2=320,gw3=255,gw4=245,gw5=230;
  int gx1=PAD, gx2=gx1+gw1+PAD, gx3=gx2+gw2+PAD, gx4=gx3+gw3+PAD, gx5=gx4+gw4+PAD;
  drawFlexHistory(gx1,gy,gw1,gh);
  drawIRWaveform(gx2,gy,gw2,gh);
  drawGSRHistory(gx3,gy,gw3,gh);
  drawHRHistory(gx4,gy,gw4,gh);
  drawTempHistory(gx5,gy,gw5,gh);
}

void drawFlexHistory(int gx, int gy, int gw, int gh) {
  noStroke(); fill(PANEL2); rect(gx,gy,gw,gh,6);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("FLEX HISTORY — Kalman (bright) vs Raw (dim)", gx+10, gy+8);
  int px=gx+38, py=gy+22, pw=gw-50, ph=gh-36;
  drawYGrid(gx,gy,gw,gh,px,py,pw,ph,0,100,4,"%");
  for (int f=0; f<5; f++) {
    if (!fxCal[f]) continue;
    color c=FC[f];
    stroke(red(c)*0.3+40,green(c)*0.3,blue(c)*0.3,90);
    strokeWeight(1); noFill(); beginShape();
    for (int i=0;i<HIST;i++) {
      int idx=(hHead+i)%HIST;
      float p=constrain(map(hfRaw[f][idx],fxRest[f],fxBend[f],0,100),0,100);
      vertex(px+(i/(float)(HIST-1))*pw, py+ph-p/100.0*ph);
    }
    endShape();
    stroke(c); strokeWeight(1.5); noFill(); beginShape();
    for (int i=0;i<HIST;i++) {
      int idx=(hHead+i)%HIST;
      float p=constrain(map(hfKal[f][idx],fxRest[f],fxBend[f],0,100),0,100);
      vertex(px+(i/(float)(HIST-1))*pw, py+ph-p/100.0*ph);
    }
    endShape();
  }
  String[] dispNames={"INDEX","MIDDLE","RING","PINKY","THUMB"};
  for (int slot=0; slot<5; slot++) {
    int f=cardOrder[slot];
    int lx=px+slot*88;
    noStroke(); fill(FC[f]); ellipse(lx+5,gy+gh-8,6,6);
    fill(THMID); textFont(fTiny); textSize(9); textAlign(LEFT,CENTER);
    text(dispNames[slot], lx+12, gy+gh-8);
  }
}

void drawIRWaveform(int gx, int gy, int gw, int gh) {
  noStroke(); fill(PANEL2); rect(gx,gy,gw,gh,6);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("MAX30102 — IR & RED RAW WAVEFORM", gx+10, gy+8);
  int px=gx+38, py=gy+22, pw=gw-50, ph=gh-36;
  int irMin=2147483647, irMax=0;
  for (int i=0;i<HIST;i++) if(hIR[i]>0){irMin=min(irMin,hIR[i]);irMax=max(irMax,hIR[i]);}
  if (irMax<=irMin){irMin=0;irMax=20000;}
  int ir=irMax-irMin, yLo=irMin-ir/10, yHi=irMax+ir/10;
  if (yLo<0) yLo=0;
  for (int d=0;d<=4;d++) {
    float frac=d/4.0, yl=py+ph-frac*ph;
    stroke(BORDER); strokeWeight(1); line(px,yl,px+pw,yl);
    fill(THDIM); textFont(fTiny); textSize(8); textAlign(RIGHT,CENTER);
    text(nf((int)(yLo+(yHi-yLo)*frac),1,0), px-4, yl);
  }
  stroke(HR_C); strokeWeight(1.5); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hIR[idx],yLo,yHi,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
  stroke(color(255,140,80)); strokeWeight(1); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hRED[idx],yLo,yHi,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;if(hBeat[idx]==1){float x=px+(i/(float)(HIST-1))*pw;stroke(color(255,255,80,180));strokeWeight(1);line(x,py,x,py+ph);}}
  noStroke(); fill(HR_C); ellipse(px+5,gy+gh-8,6,6);
  fill(THMID); textFont(fTiny); textSize(9); textAlign(LEFT,CENTER); text("IR",px+12,gy+gh-8);
  noStroke(); fill(color(255,140,80)); ellipse(px+35,gy+gh-8,6,6);
  fill(THMID); text("RED",px+42,gy+gh-8);
  noStroke(); fill(color(255,255,80)); ellipse(px+80,gy+gh-8,6,6);
  fill(THMID); text("BEAT",px+87,gy+gh-8);
  fill(lastBPM>0?HR_C:THDIM); textFont(fMed); textSize(13); textAlign(RIGHT,TOP);
  text(lastBPM>0?str(lastBPM)+" BPM":"-- BPM", gx+gw-10, gy+10);
}

void drawGSRHistory(int gx, int gy, int gw, int gh) {
  noStroke(); fill(PANEL2); rect(gx,gy,gw,gh,6);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("GSR — Conductance", gx+10, gy+8);
  int px=gx+38, py=gy+22, pw=gw-50, ph=gh-36;
  drawYGrid(gx,gy,gw,gh,px,py,pw,ph,800,3000,4,"");
  stroke(red(GSR_C)*0.4+60,60,40,100); strokeWeight(1); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hgRaw[idx],800,3000,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
  stroke(GSR_C); strokeWeight(1.5); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hgKal[idx],800,3000,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
  noStroke(); fill(GSR_C); ellipse(px+5,gy+gh-8,6,6);
  fill(THMID); textFont(fTiny); textSize(9); textAlign(LEFT,CENTER); text("KAL",px+12,gy+gh-8);
}

void drawHRHistory(int gx, int gy, int gw, int gh) {
  noStroke(); fill(PANEL2); rect(gx,gy,gw,gh,6);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("HR / SpO₂", gx+10, gy+8);
  int px=gx+38, py=gy+22, pw=gw-50, ph=gh-36;
  drawYGrid(gx,gy,gw,gh,px,py,pw,ph,40,120,4,"");
  stroke(HR_C); strokeWeight(1.5); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hBPM[idx],40,120,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
  stroke(SPO_C); strokeWeight(1); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hSPO2[idx],85,100,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
  noStroke(); fill(HR_C); ellipse(px+5,gy+gh-8,6,6);
  fill(THMID); textFont(fTiny); textSize(9); textAlign(LEFT,CENTER); text("BPM",px+12,gy+gh-8);
  noStroke(); fill(SPO_C); ellipse(px+50,gy+gh-8,6,6);
  fill(THMID); text("SpO₂",px+57,gy+gh-8);
}

void drawTempHistory(int gx, int gy, int gw, int gh) {
  noStroke(); fill(PANEL2); rect(gx,gy,gw,gh,6);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(LEFT,TOP);
  text("SKIN TEMP", gx+10, gy+8);
  int px=gx+38, py=gy+22, pw=gw-50, ph=gh-36;
  drawYGrid(gx,gy,gw,gh,px,py,pw,ph,28,40,4,"°C");
  stroke(TMP_C); strokeWeight(1.5); noFill(); beginShape();
  for (int i=0;i<HIST;i++){int idx=(hHead+i)%HIST;float p=constrain(map(hTemp[idx],28,40,0,1),0,1);vertex(px+(i/(float)(HIST-1))*pw,py+ph-p*ph);}
  endShape();
}

// ═══════════════════════════════════════════════════════════════
//  REUSABLE HELPERS
// ═══════════════════════════════════════════════════════════════
void drawYGrid(int gx, int gy, int gw, int gh, int px, int py, int pw, int ph,
               float yMin, float yMax, int divs, String unit) {
  for (int d=0; d<=divs; d++) {
    float frac=d/(float)divs, yl=py+ph-frac*ph, val=yMin+(yMax-yMin)*frac;
    stroke(BORDER); strokeWeight(1); line(px,yl,px+pw,yl);
    fill(THDIM); textFont(fTiny); textSize(8); textAlign(RIGHT,CENTER);
    text(nf(val,1,0)+unit, px-4, yl);
  }
}

void panelBg(int cx, int cy, int cw, int ch, color accent) {
  noStroke(); fill(PANEL); rect(cx,cy,cw,ch,6);
  fill(accent); rect(cx,cy,cw,3,6,6,0,0);
}

// ═══════════════════════════════════════════════════════════════
//  FOOTER
// ═══════════════════════════════════════════════════════════════
void drawFooter() {
  noStroke(); fill(PANEL2); rect(0,H-FTR_H,W,FTR_H);
  fill(THDIM); textFont(fTiny); textSize(9); textAlign(RIGHT,CENTER);
  text(nf(frameRate,1,0)+" fps  •  PKT:"+totalPackets+"  •  "+(portOpen?COM_PORT:"NO PORT"), W-14, H-FTR_H/2);
}
