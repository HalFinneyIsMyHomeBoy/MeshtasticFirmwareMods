#define LED_POWER LED

#define USE_SSD1306 // Heltec_v3 has a SSD1306 display

#define RESET_OLED RST_OLED
#define I2C_SDA SDA_OLED // I2C pins for this board
#define I2C_SCL SCL_OLED

// Enable secondary bus for external periherals
#define I2C_SDA1 SDA
#define I2C_SCL1 SCL

#define VEXT_ENABLE Vext // active low, powers the oled display and the lora antenna boost
#define BUTTON_PIN 0

#define ADC_CTRL 37
#define ADC_CTRL_ENABLED LOW
#define BATTERY_PIN 1 // A battery voltage measurement pin, voltage divider connected here to measure battery voltage
#define ADC_CHANNEL ADC_CHANNEL_0
#define ADC_ATTENUATION ADC_ATTEN_DB_2_5 // lower dB for high resistance voltage divider
#define ADC_MULTIPLIER 4.9 * 1.045

#define USE_SX1262

#define LORA_DIO0 -1 // a No connect on the SX1262 module
#define LORA_RESET 12
#define LORA_DIO1 14 // SX1262 IRQ
#define LORA_DIO2 13 // SX1262 BUSY
#define LORA_DIO3    // Not connected on PCB, but internally on the TTGO SX1262, if DIO3 is high the TXCO is enabled

#define LORA_SCK 9
#define LORA_MISO 11
#define LORA_MOSI 10
#define LORA_CS 8

#define SX126X_CS LORA_CS
#define SX126X_DIO1 LORA_DIO1
#define SX126X_BUSY LORA_DIO2
#define SX126X_RESET LORA_RESET

#define SX126X_DIO2_AS_RF_SWITCH
#define SX126X_DIO3_TCXO_VOLTAGE 1.8

#define HAS_32768HZ 1

// Pulse GPIO_TRIGGER_PIN when a direct message exactly matches GPIO_TRIGGER_MESSAGE.
#define GPIO_TRIGGER_PIN 7
#define GPIO_TRIGGER_MESSAGE "Open_Seseme"
#define GPIO_TRIGGER_MS 10000

// Read VOLTAGE_ADC_PIN and DM the result when a direct message matches VOLTAGE_QUERY_MESSAGE.
#define VOLTAGE_QUERY_MESSAGE "Get_Reading"
#define VOLTAGE_ADC_PIN 6 // Heltec V3 J3 pin 17; 0-3.3V at the pin (use a divider for higher voltages)
#define VOLTAGE_ADC_CHANNEL ADC_CHANNEL_5
#define VOLTAGE_ADC_ATTENUATION ADC_ATTEN_DB_12
#define VOLTAGE_ADC_MULTIPLIER 1.0f
