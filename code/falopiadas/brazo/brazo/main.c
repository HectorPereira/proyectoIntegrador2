#define F_CPU 16000000UL
#include <avr/io.h>
#include <util/delay.h>
#include <avr/interrupt.h>

#define SERVO_MIN_US 1000
#define SERVO_MAX_US 2000
#define SERVO_PERIOD_US 20000

const uint8_t potPins[4]  = {0, 1, 2, 3};   // ADC0–ADC3
const uint8_t servoPins[4]= {PD6, PB1, PB2, PB3};

uint16_t potCenter[4];
uint16_t pulseWidth[4]; // in microseconds

// ---------------------------------------------------------------------
// ADC
// ---------------------------------------------------------------------
void ADC_init(void) {
	ADMUX  = (1 << REFS0);                         // AVcc ref
	ADCSRA = (1 << ADEN) | (1 << ADPS2) | (1 << ADPS1) | (1 << ADPS0); // /128
}
uint16_t ADC_read(uint8_t ch) {
	ADMUX = (ADMUX & 0xF8) | (ch & 0x07);
	ADCSRA |= (1 << ADSC);
	while (ADCSRA & (1 << ADSC));
	return ADC;
}

// ---------------------------------------------------------------------
// Timer1 free-running microsecond counter (for 20 ms frame)
// ---------------------------------------------------------------------
void timer1_init(void) {
	TCCR1A = 0;
	TCCR1B = (1 << CS11);          // prescaler 8 ? 0.5 µs per tick
}

// ---------------------------------------------------------------------
// Map helpers
// ---------------------------------------------------------------------
long map_value(long x,long in_min,long in_max,long out_min,long out_max){
	return (x - in_min)*(out_max - out_min)/(in_max - in_min)+out_min;
}

// ---------------------------------------------------------------------
// Generate pulses sequentially (software PWM)
// ---------------------------------------------------------------------
void servo_pulse_frame(void) {
	// All outputs low initially
	PORTD &= ~(1<<PD6);
	PORTB &= ~((1<<PB1)|(1<<PB2)|(1<<PB3));

	// Turn all high at start
	PORTD |= (1<<PD6);
	PORTB |= (1<<PB1)|(1<<PB2)|(1<<PB3);

	uint16_t start = TCNT1;
	for (;;) {
		uint16_t now = TCNT1;
		uint16_t elapsed_us = (now - start)/2; // prescaler 8 ? 0.5 µs/tick

		if (elapsed_us >= SERVO_PERIOD_US) break;

		if (elapsed_us >= pulseWidth[0]) PORTD &= ~(1<<PD6);
		if (elapsed_us >= pulseWidth[1]) PORTB &= ~(1<<PB1);
		if (elapsed_us >= pulseWidth[2]) PORTB &= ~(1<<PB2);
		if (elapsed_us >= pulseWidth[3]) PORTB &= ~(1<<PB3);
	}
}

// ---------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------
int main(void) {
	DDRD |= (1<<PD6);
	DDRB |= (1<<PB1)|(1<<PB2)|(1<<PB3);

	ADC_init();
	timer1_init();

	// --- Calibration ---
	for(uint8_t i=0;i<4;i++){
		potCenter[i]=ADC_read(potPins[i]);
		_delay_ms(5);
	}

	while(1){
		for(uint8_t i=0;i<4;i++){
			uint16_t val = ADC_read(potPins[i]);

			// Convert ADC (0–1023) to potentiometer angle (0–270°)
			long pot_angle = map_value(val, 0, 1023, 0, 270);

			// Convert to servo command (1:1, not compressed)
			long angle = pot_angle;  // direct proportional mapping

			// Calibrate around center if you want 90° at current pot position
			long offset = map_value(potCenter[i], 0, 1023, 0, 270);
			angle = 90 + (pot_angle - offset);

			// Limit to servo capability
			if (angle < 0) angle = 0;
			if (angle > 180) angle = 180;

			switch(i){
				case 0: angle = 180 - angle; break;
				case 1: angle = 180 - angle; if(angle<30) angle=30; if(angle>150) angle=150; break;
				case 2: break;
				case 3: angle = 180 - angle; break;
			}
			pulseWidth[i] = SERVO_MIN_US + (angle * (SERVO_MAX_US - SERVO_MIN_US)) / 180;
		}

		// Emit one 20 ms pulse frame for all servos
		servo_pulse_frame();
	}
}
