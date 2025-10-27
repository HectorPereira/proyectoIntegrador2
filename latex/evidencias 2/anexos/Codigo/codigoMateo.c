#define F_CPU 16000000UL
#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>

/* ===== Servo (D9/OC1A) ===== */
#define SERVO_MIN_US     500
#define SERVO_MAX_US     2500
#define SERVO_SLEW_STEP  2

static inline void servo_init(void){
	DDRB  |= (1<<PB1);
	TCCR1A = (1<<COM1A1) | (1<<WGM11);
	TCCR1B = (1<<WGM13)  | (1<<WGM12) | (1<<CS11); // presc=8
	ICR1 = 39999; // 20 ms
}
static inline uint16_t us_to_ticks(uint16_t us){ return (uint16_t)(us*2); }
static inline void servoA_write_us(uint16_t us){
	if(us<SERVO_MIN_US) us=SERVO_MIN_US;
	if(us>SERVO_MAX_US) us=SERVO_MAX_US;
	OCR1A = us_to_ticks(us);
}
static inline uint16_t deg_to_us(uint8_t deg){
	if(deg>180) deg=180;
	return (uint16_t)(SERVO_MIN_US + ((uint32_t)(SERVO_MAX_US-SERVO_MIN_US)*deg)/180);
}

/* ===== ADC (A0) ===== */
#define ADC_SAMPLES 10
#define ADC_DEADBAND 2
static inline void adc_init(void){
	ADMUX  = (1<<REFS0);
	ADCSRA = (1<<ADEN) | (1<<ADPS2) | (1<<ADPS1); // /64
}
static inline uint16_t adc_read(uint8_t ch){
	ADMUX = (ADMUX & 0xF0) | (ch & 0x0F);
	ADCSRA |= (1<<ADSC);
	while(ADCSRA & (1<<ADSC));
	return ADC;
}
static inline uint16_t adc_read_avg(uint8_t ch, uint8_t n){
	uint32_t s=0; for(uint8_t i=0;i<n;i++) s+=adc_read(ch); return (uint16_t)(s/n);
}
static inline uint8_t pot_to_deg(uint16_t v){
	uint32_t ang = ((uint32_t)v*180)/1023; if(ang>180) ang=180; return (uint8_t)ang;
}
static inline uint8_t smooth_move(uint8_t a, uint8_t t){
	if(a<t){ uint8_t d=t-a; a += (d>SERVO_SLEW_STEP)?SERVO_SLEW_STEP:d; }
	else if(a>t){ uint8_t d=a-t; a -= (d>SERVO_SLEW_STEP)?SERVO_SLEW_STEP:d; }
	return a;
}

// UART 115200 (U2X=1) + RX por interrupcion
static void uart_init(uint32_t baud){
	UCSR0A = (1<<U2X0);                         // doble velocidad
	uint16_t ubrr = (uint16_t)(F_CPU/8/baud - 1);
	UBRR0H = (uint8_t)(ubrr>>8);
	UBRR0L = (uint8_t)(ubrr&0xFF);
	UCSR0B = (1<<RXEN0) | (1<<TXEN0) | (1<<RXCIE0);  // habilita RX, TX e INT RX
	UCSR0C = (1<<UCSZ01) | (1<<UCSZ00);         // 8N1
}
static inline void uart_tx(uint8_t c){ while(!(UCSR0A&(1<<UDRE0))); UDR0=c; }
static void uart_write(const char* s){ while(*s) uart_tx((uint8_t)*s++); }

// Ring buffer RX (tamano potencia de 2)
#define RX_BUF_SZ 128
static volatile char    rx_buf[RX_BUF_SZ];
static volatile uint8_t rx_head=0, rx_tail=0;

ISR(USART_RX_vect){
	uint8_t nh = (rx_head + 1) & (RX_BUF_SZ-1);
	char c = UDR0;                   // leer SIEMPRE UDR0
	if(nh != rx_tail){               // si hay espacio
		rx_buf[rx_head] = c;
		rx_head = nh;
		} // si se llena, descarta byte
	}

	static bool rb_get_char(char *out){
		if(rx_head == rx_tail) return false;
		*out = rx_buf[rx_tail];
		rx_tail = (rx_tail + 1) & (RX_BUF_SZ-1);
		return true;
	}

	// Parser helpers
	static inline void to_upper_str(char *s){ for(;*s;++s) if(*s>='a'&&*s<='z') *s+='A'-'a'; }
	static inline bool is_printable(unsigned char c){ return (c>=32 && c<=126); }
	static void compact_colon_spaces(char *s){
		char t[96]; uint8_t k=0;
		for(uint8_t i=0; s[i] && k<sizeof(t)-1; ++i){
			if(s[i]==':'){
				while(k>0 && (t[k-1]==' '||t[k-1]=='\t')) k--;
				t[k++]=':'; uint8_t m=i+1; while(s[m]==' '||s[m]=='\t') m++; i=m-1;
			} else t[k++]=s[i];
		}
		t[k]='\0'; strcpy(s,t);
	}

	// Estado y modos
	typedef enum { MODE_POT=0, MODE_REMOTE=1 } mode_t;
	static volatile mode_t mode = MODE_POT;
	static uint8_t angA=90, targetA=90;

	// Ensamblador de lineas
	static char line[96]; static uint8_t li=0;
	static void process_line(char *ln){
		// limpiar no-imprimibles
		char s[96]; uint8_t j=0;
		for(uint8_t i=0; ln[i] && j<sizeof(s)-1; ++i){
			unsigned char c=(unsigned char)ln[i];
			if(is_printable(c)) s[j++]=(char)c;
		}
		s[j]='\0';
		to_upper_str(s);
		compact_colon_spaces(s);

		if (strncmp(s,"S:",2)==0){
			int v=-1;
			if (sscanf(s+2,"%d",&v)==1 && v>=0 && v<=180){
				targetA = (uint8_t)v;
				mode = MODE_REMOTE;
				angA = targetA;
				servoA_write_us(deg_to_us(angA));
				char msg[24]; snprintf(msg,sizeof(msg),"OK S:%u\n",angA); uart_write(msg);
			} else uart_write("ERR\n");

			} else if (strncmp(s,"MODE:POT",8)==0){
			mode = MODE_POT; uart_write("OK\n");

			} else if (strncmp(s,"MODE:REMOTE",11)==0){
			mode = MODE_REMOTE; uart_write("OK\n");

			} else if (s[0]=='Q'){
			char msg[40]; snprintf(msg,sizeof(msg),"ANG=%u,MODE=%s\n",angA,(mode==MODE_REMOTE)?"REMOTE":"POT");
			uart_write(msg);

			} else if (strncmp(s,"RAW:",4)==0){
			int us=-1;
			if (sscanf(s+4,"%d",&us)==1 && us>=SERVO_MIN_US && us<=SERVO_MAX_US){
				mode = MODE_REMOTE;
				servoA_write_us((uint16_t)us);
				uint32_t d=(uint32_t)(us - SERVO_MIN_US) * 180 / (SERVO_MAX_US - SERVO_MIN_US);
				if(d>180) d=180;
				angA=(uint8_t)d; targetA=angA; uart_write("OK\n");
			} else uart_write("ERR\n");

			} else if (s[0]=='\0'){
			// linea vacia -> ignorar
			} else {
			char dbg[64]; snprintf(dbg,sizeof(dbg),"ERR RX:\"%s\"\n", s); uart_write(dbg);
		}
	}

	int main(void){
		servo_init();
		adc_init();
		uart_init(115200);
		sei();

		servoA_write_us(deg_to_us(angA));
		_delay_ms(200);
		uart_write("READY\n");

		for(;;){
			// Consume bytes de la cola y arma lineas (CR/LF terminan linea)
			char c;
			while(rb_get_char(&c)){
				if(c=='\r' || c=='\n'){
					if(li>0){ line[li]='\0'; process_line(line); li=0; }
					} else {
					if(li < sizeof(line)-1) line[li++]=c;
				}
			}

			// MODO POT
			if (mode == MODE_POT){
				uint16_t p = adc_read_avg(0, ADC_SAMPLES);
				uint8_t pdeg = pot_to_deg(p);
				if( (pdeg>targetA && (pdeg-targetA)>=ADC_DEADBAND) ||
				(pdeg<targetA && (targetA-pdeg)>=ADC_DEADBAND) ){
					targetA = pdeg;
				}
			}

			// Slew + PWM
			angA = smooth_move(angA, targetA);
			servoA_write_us(deg_to_us(angA));

			_delay_ms(10);
		}
	}
