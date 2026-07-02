#ifdef HELTEC_V3

#include "platform/extra_variants/heltec_v3/GpioTriggerModule.h"
#include "NodeDB.h"
#include "configuration.h"
#include "main.h"
#include <Arduino.h>
#include <string.h>

GpioTriggerModule::GpioTriggerModule()
    : SinglePortModule("GpioTrigger", meshtastic_PortNum_TEXT_MESSAGE_APP), concurrency::OSThread("GpioTrigger")
{
    pinMode(GPIO_TRIGGER_PIN, OUTPUT);
    digitalWrite(GPIO_TRIGGER_PIN, LOW);
}

ProcessMessage GpioTriggerModule::handleReceived(const meshtastic_MeshPacket &mp)
{
    if (isFromUs(&mp) || isBroadcast(mp.to) || !isToUs(&mp))
        return ProcessMessage::CONTINUE;

    auto &p = mp.decoded;
    static const char trigger[] = GPIO_TRIGGER_MESSAGE;
    const size_t triggerLen = sizeof(GPIO_TRIGGER_MESSAGE) - 1;

    if (p.payload.size != triggerLen || memcmp(p.payload.bytes, trigger, triggerLen) != 0)
        return ProcessMessage::CONTINUE;

    LOG_INFO("GpioTrigger: matched DM, pulsing GPIO %d", GPIO_TRIGGER_PIN);
    digitalWrite(GPIO_TRIGGER_PIN, HIGH);
    offAtMs = millis() + GPIO_TRIGGER_MS;
    enabled = true;
    setIntervalFromNow(50);

    return ProcessMessage::CONTINUE;
}

int32_t GpioTriggerModule::runOnce()
{
    if (offAtMs == 0 || millis() < offAtMs)
        return 50;

    digitalWrite(GPIO_TRIGGER_PIN, LOW);
    offAtMs = 0;
    return INT32_MAX;
}

#endif
