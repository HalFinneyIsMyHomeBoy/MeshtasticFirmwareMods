#ifdef HELTEC_V3

#include "platform/extra_variants/heltec_v3/VoltageQueryModule.h"
#include "MeshService.h"
#include "NodeDB.h"
#include "configuration.h"
#include "main.h"
#include "power.h"
#include <Arduino.h>
#include <string.h>

#ifdef ARCH_ESP32
#include <soc/adc_channel.h>
#endif

static bool voltageAdcConfigured = false;

static void ensureVoltageAdcConfigured()
{
    if (voltageAdcConfigured)
        return;

    pinMode(VOLTAGE_ADC_PIN, INPUT);
    voltageAdcConfigured = true;
}

VoltageQueryModule::VoltageQueryModule() : SinglePortModule("VoltageQuery", meshtastic_PortNum_TEXT_MESSAGE_APP)
{
    ensureVoltageAdcConfigured();
}

float VoltageQueryModule::readPinVoltage()
{
    ensureVoltageAdcConfigured();

    int avgMv = 0;
#ifdef ARCH_ESP32
    if (!esp32ReadAdc1ChannelMillivolts(VOLTAGE_ADC_CHANNEL, VOLTAGE_ADC_ATTENUATION, &avgMv)) {
        LOG_ERROR("VoltageQuery: ADC read failed on GPIO %d", VOLTAGE_ADC_PIN);
        return 0.0f;
    }
#else
    uint32_t sum = 0;
    constexpr int samples = 8;
    for (int i = 0; i < samples; i++)
        sum += analogRead(VOLTAGE_ADC_PIN);
    avgMv = (sum / samples) * 3300 / 4095;
#endif

    LOG_INFO("VoltageQuery: GPIO %d read %dmV", VOLTAGE_ADC_PIN, avgMv);
    return (avgMv / 1000.0f) * VOLTAGE_ADC_MULTIPLIER;
}

void VoltageQueryModule::sendDm(const meshtastic_MeshPacket &rx, const char *text)
{
    if (!text)
        return;

    meshtastic_MeshPacket *p = allocDataPacket();
    p->to = rx.from;
    p->channel = rx.channel;
    p->want_ack = false;
    p->decoded.want_response = false;

    size_t len = strlen(text);
    if (len > sizeof(p->decoded.payload.bytes))
        len = sizeof(p->decoded.payload.bytes);

    p->decoded.payload.size = len;
    memcpy(p->decoded.payload.bytes, text, len);
    service->sendToMesh(p);
}

ProcessMessage VoltageQueryModule::handleReceived(const meshtastic_MeshPacket &mp)
{
    if (isFromUs(&mp) || isBroadcast(mp.to) || !isToUs(&mp))
        return ProcessMessage::CONTINUE;

    auto &p = mp.decoded;
    static const char query[] = VOLTAGE_QUERY_MESSAGE;
    const size_t queryLen = sizeof(VOLTAGE_QUERY_MESSAGE) - 1;

    if (p.payload.size != queryLen || memcmp(p.payload.bytes, query, queryLen) != 0)
        return ProcessMessage::CONTINUE;

    const uint32_t now = millis();
    if (mp.from == lastReplyFrom && (now - lastReplyMs) < 2000)
        return ProcessMessage::CONTINUE;

    const float volts = readPinVoltage();
    char reply[48];
    snprintf(reply, sizeof(reply), "Voltage: %.3fV", volts);

    LOG_INFO("VoltageQuery: replying %.3fV to 0x%08x", volts, mp.from);
    sendDm(mp, reply);

    lastReplyMs = now;
    lastReplyFrom = mp.from;

    return ProcessMessage::CONTINUE;
}

#endif
