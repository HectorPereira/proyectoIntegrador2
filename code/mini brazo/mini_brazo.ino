// minibrazo_usb.ino  (envía POT por USB/Serial)
const int PINS[4]={A1,A2,A3,A4};
int val[4], prevv[4];
const int DEADBAND=3;
const float ALPHA=0.3f;

int ema(int last,int now){ return (int)(ALPHA*now + (1.0f-ALPHA)*last); }

void setup(){
  Serial.begin(115200);               // <-- BAUD para la app (poné "Baud (Mini)" = 115200)
  for(int i=0;i<4;i++){ val[i]=prevv[i]=analogRead(PINS[i]); }
}

void loop(){
  bool changed=false;
  for(int i=0;i<4;i++){
    int raw=analogRead(PINS[i]);
    int f=ema(val[i],raw);
    val[i]=f;
    if(abs(f-prevv[i])>=DEADBAND){ prevv[i]=f; changed=true; }
  }
  if(changed){
    Serial.print("POT "); Serial.print(prevv[0]); Serial.print(' ');
    Serial.print(prevv[1]); Serial.print(' '); Serial.print(prevv[2]); Serial.print(' ');
    Serial.print(prevv[3]); Serial.print('\n');
  }
  delay(15); // ~66 Hz
}
