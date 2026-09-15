#ifdef HELTEC_V3

#include "platform/extra_variants/heltec_v3/DmTriggerModule.h"
#include "FSCommon.h"
#include "MeshService.h"
#include "NodeDB.h"
#include "SPILock.h"
#include "SafeFile.h"
#include "concurrency/LockGuard.h"
#include "configuration.h"
#include "main.h"
#include "power.h"
#include <Arduino.h>
#include <ctype.h>
#include <string.h>

#ifdef ARCH_ESP32
#include <soc/adc_channel.h>
#endif

namespace
{
constexpr uint32_t kStateMagic = 0x444D5452; // 'DMTR'
constexpr uint8_t kStateVersion = 1;
constexpr const char *kStateFile = "/prefs/dm_triggers.bin";
constexpr uint32_t kAnalogReplyCooldownMs = 2000;
constexpr char kConfigPrefix[] = "!dmtrigger:";

#pragma pack(push, 1)
struct PersistedTrigger {
    uint8_t enabled;
    uint8_t type;
    uint8_t gpio;
    uint8_t adcAtten;
    uint32_t outputDurationMs;
    float adcMultiplier;
    char name[DmTriggerModule::kNameLen];
    char message[DmTriggerModule::kMessageLen];
};

struct PersistedState {
    uint32_t magic;
    uint8_t version;
    uint8_t count;
    uint8_t reserved[2];
    PersistedTrigger triggers[DmTriggerModule::kMaxTriggers];
};
#pragma pack(pop)

static bool copyStringField(char *dest, size_t destLen, const char *src)
{
    if (!dest || destLen == 0)
        return false;
    if (!src)
        src = "";
    strncpy(dest, src, destLen - 1);
    dest[destLen - 1] = '\0';
    return true;
}

#ifdef ARCH_ESP32
static bool gpioToAdc1Channel(uint8_t gpio, adc_channel_t *channelOut)
{
    if (!channelOut || gpio < 1 || gpio > 10)
        return false;
    *channelOut = (adc_channel_t)(gpio - 1);
    return true;
}
#endif

static bool isReservedHeltecGpio(uint8_t gpio)
{
    static const uint8_t reserved[] = {0, 8, 9, 10, 11, 12, 13, 14, 17, 18, 21, 37};
    for (uint8_t pin : reserved) {
        if (gpio == pin)
            return true;
    }
    return false;
}

static const char *triggerTypeName(DmTriggerModule::TriggerType type)
{
    return type == DmTriggerModule::TriggerType::AnalogReading ? "analog" : "output";
}

static bool parseTriggerType(const char *text, DmTriggerModule::TriggerType &typeOut)
{
    if (!text)
        return false;
    if (strcasecmp(text, "output") == 0) {
        typeOut = DmTriggerModule::TriggerType::Output;
        return true;
    }
    if (strcasecmp(text, "analog") == 0 || strcasecmp(text, "analogreading") == 0) {
        typeOut = DmTriggerModule::TriggerType::AnalogReading;
        return true;
    }
    return false;
}

static char *trimToken(char *token)
{
    while (*token == ' ')
        token++;
    char *end = token + strlen(token);
    while (end > token && end[-1] == ' ')
        end--;
    *end = '\0';
    return token;
}
} // namespace

DmTriggerModule::DmTriggerModule() : SinglePortModule("DmTrigger", meshtastic_PortNum_TEXT_MESSAGE_APP), OSThread("DmTrigger")
{
    bool hadFile = false;
    bool loadedOk = loadFromDisk(hadFile);
    if (triggerCount == 0 && !hadFile) {
        installDefaultTriggers();
    } else if (triggerCount == 0 && hadFile && !loadedOk) {
        LOG_ERROR("DmTrigger: prefs file present but invalid — not installing defaults (avoids clobbering)");
    }
    LOG_INFO("DmTrigger: loaded %u trigger(s)", triggerCount);
    for (uint8_t i = 0; i < triggerCount; i++) {
        LOG_INFO("DmTrigger: [%u] enabled=%u type=%u gpio=%u msg='%s'", i, triggers[i].enabled, (unsigned)triggers[i].type,
                 triggers[i].gpio, triggers[i].message);
    }
    setIntervalFromNow(50);
}

const DmTriggerModule::Trigger *DmTriggerModule::getTrigger(uint8_t index) const
{
    if (index >= triggerCount)
        return nullptr;
    return &triggers[index];
}

bool DmTriggerModule::setTrigger(uint8_t index, const Trigger &trigger)
{
    if (index >= kMaxTriggers)
        return false;
    if (!isGpioAllowed(trigger.gpio, trigger.type))
        return false;
    if (trigger.message[0] == '\0')
        return false;

    triggers[index] = trigger;
    triggers[index].enabled = true;
    if (index >= triggerCount)
        triggerCount = index + 1;
    return saveToDisk();
}

bool DmTriggerModule::appendTrigger(const Trigger &trigger)
{
    if (triggerCount >= kMaxTriggers)
        return false;
    return setTrigger(triggerCount, trigger);
}

bool DmTriggerModule::removeTrigger(uint8_t index)
{
    if (index >= triggerCount)
        return false;

    for (uint8_t i = index; i + 1 < triggerCount; i++)
        triggers[i] = triggers[i + 1];
    memset(&triggers[triggerCount - 1], 0, sizeof(Trigger));
    triggerCount--;
    return saveToDisk();
}

void DmTriggerModule::clearTriggers()
{
    memset(triggers, 0, sizeof(triggers));
    triggerCount = 0;
    saveToDisk();
}

void DmTriggerModule::installDefaultTriggers()
{
    triggerCount = 0;

#ifdef GPIO_TRIGGER_PIN
    Trigger output{};
    output.enabled = true;
    output.type = TriggerType::Output;
    output.gpio = GPIO_TRIGGER_PIN;
    output.outputDurationMs = GPIO_TRIGGER_MS;
    copyStringField(output.name, sizeof(output.name), "GPIO Output");
    copyStringField(output.message, sizeof(output.message), GPIO_TRIGGER_MESSAGE);
    appendTrigger(output);
#endif

#ifdef VOLTAGE_ADC_PIN
    Trigger analog{};
    analog.enabled = true;
    analog.type = TriggerType::AnalogReading;
    analog.gpio = VOLTAGE_ADC_PIN;
#ifdef VOLTAGE_ADC_ATTENUATION
    analog.adcAtten = VOLTAGE_ADC_ATTENUATION;
#else
    analog.adcAtten = ADC_ATTEN_DB_12;
#endif
#ifdef VOLTAGE_ADC_MULTIPLIER
    analog.adcMultiplier = VOLTAGE_ADC_MULTIPLIER;
#else
    analog.adcMultiplier = 1.0f;
#endif
    copyStringField(analog.name, sizeof(analog.name), "Voltage");
    copyStringField(analog.message, sizeof(analog.message), VOLTAGE_QUERY_MESSAGE);
    appendTrigger(analog);
#endif

    saveToDisk();
}

void DmTriggerModule::loadFromDisk()
{
    bool unused = false;
    (void)loadFromDisk(unused);
}

bool DmTriggerModule::loadFromDisk(bool &hadFile)
{
    hadFile = false;
#ifdef FSCom
    concurrency::LockGuard g(spiLock);
    auto file = FSCom.open(kStateFile, FILE_O_READ);
    if (!file) {
        LOG_INFO("DmTrigger: no prefs at %s", kStateFile);
        return false;
    }
    hadFile = true;

    PersistedState state{};
    const size_t got = file.read(reinterpret_cast<uint8_t *>(&state), sizeof(state));
    file.close();
    if (got != sizeof(state) || state.magic != kStateMagic || state.version != kStateVersion || state.count > kMaxTriggers) {
        LOG_WARN("DmTrigger: invalid prefs (got=%u expect=%u magic=0x%08x ver=%u count=%u)", (unsigned)got,
                 (unsigned)sizeof(state), state.magic, state.version, state.count);
        return false;
    }

    triggerCount = 0;
    memset(triggers, 0, sizeof(triggers));
    for (uint8_t i = 0; i < state.count; i++) {
        const PersistedTrigger &src = state.triggers[i];
        Trigger &dst = triggers[i];
        dst.enabled = src.enabled != 0;
        dst.type = (src.type == (uint8_t)TriggerType::AnalogReading) ? TriggerType::AnalogReading : TriggerType::Output;
        dst.gpio = src.gpio;
        dst.outputDurationMs = src.outputDurationMs;
        dst.adcMultiplier = src.adcMultiplier;
        dst.adcAtten = src.adcAtten;
        copyStringField(dst.name, sizeof(dst.name), src.name);
        copyStringField(dst.message, sizeof(dst.message), src.message);
        triggerCount++;
    }
    LOG_INFO("DmTrigger: restored %u trigger(s) from disk", triggerCount);
    return true;
#else
    return false;
#endif
}

bool DmTriggerModule::saveToDisk() const
{
#ifdef FSCom
    PersistedState state{};
    state.magic = kStateMagic;
    state.version = kStateVersion;
    state.count = triggerCount;

    for (uint8_t i = 0; i < triggerCount; i++) {
        const Trigger &src = triggers[i];
        PersistedTrigger &dst = state.triggers[i];
        dst.enabled = src.enabled ? 1 : 0;
        dst.type = (uint8_t)src.type;
        dst.gpio = src.gpio;
        dst.outputDurationMs = src.outputDurationMs;
        dst.adcMultiplier = src.adcMultiplier;
        dst.adcAtten = src.adcAtten;
        copyStringField(dst.name, sizeof(dst.name), src.name);
        copyStringField(dst.message, sizeof(dst.message), src.message);
    }

    {
        concurrency::LockGuard g(spiLock);
        FSCom.mkdir("/prefs");
    }
    auto file = SafeFile(kStateFile, true);
    const size_t written = file.write(reinterpret_cast<const uint8_t *>(&state), sizeof(state));
    const bool ok = file.close() && written == sizeof(state);
    if (ok)
        LOG_INFO("DmTrigger: saved %u trigger(s) to %s", triggerCount, kStateFile);
    else
        LOG_ERROR("DmTrigger: failed to save prefs (%u of %u bytes)", (unsigned)written, (unsigned)sizeof(state));
    return ok;
#else
    return false;
#endif
}

bool DmTriggerModule::isGpioAllowed(uint8_t gpio, TriggerType type) const
{
    if (gpio == 0)
        return false;
    if (isReservedHeltecGpio(gpio))
        return false;
    if (type == TriggerType::Output && gpio == BATTERY_PIN)
        return false;
    return true;
}

bool DmTriggerModule::isAuthorizedConfigSender(const meshtastic_MeshPacket &mp) const
{
    if (isFromUs(&mp))
        return true;

    const meshtastic_NodeInfoLite *sender = nodeDB->getMeshNode(mp.from);
    if (!sender || sender->public_key.size != 32)
        return false;

    for (size_t i = 0; i < 3; i++) {
        if (config.security.admin_key[i].size == 32 &&
            memcmp(sender->public_key.bytes, config.security.admin_key[i].bytes, 32) == 0)
            return true;
    }
    return false;
}

void DmTriggerModule::sendDm(const meshtastic_MeshPacket &rx, const char *text)
{
    if (!text)
        return;

    meshtastic_MeshPacket *p = allocDataPacket();
    p->want_ack = false;
    p->decoded.want_response = false;
    p->decoded.request_id = rx.id;

    size_t len = strlen(text);
    if (len > sizeof(p->decoded.payload.bytes))
        len = sizeof(p->decoded.payload.bytes);

    p->decoded.payload.size = len;
    memcpy(p->decoded.payload.bytes, text, len);

    // USB/phone config session — deliver locally without mesh encoding.
    if (rx.from == 0) {
        p->to = nodeDB->getNodeNum();
        p->channel = rx.channel;
        service->sendToPhone(p);
        return;
    }

    // Never reply on the open channel — only DM the original sender.
    const NodeNum dest = getFrom(&rx);
    meshtastic_NodeInfoLite_public_key_t destKey{};
    if (!nodeDB->copyPublicKey(dest, destKey)) {
        LOG_WARN("DmTrigger: no public key for 0x%08x — cannot send DM reply", dest);
        packetPool.release(p);
        return;
    }

    p->to = dest;
    p->channel = 0;
    p->pki_encrypted = true;
    memcpy(p->public_key.bytes, destKey.bytes, 32);
    p->public_key.size = 32;
    LOG_INFO("DmTrigger: sending PKI reply to 0x%08x", dest);
    service->sendToMesh(p);
}

void DmTriggerModule::sendConfigReply(const meshtastic_MeshPacket &rx, const char *text)
{
    sendDm(rx, text);
}

bool DmTriggerModule::parseSetCommand(const char *args, Trigger &out, int &indexOut) const
{
    char buffer[192];
    if (!args || strlen(args) >= sizeof(buffer))
        return false;
    strncpy(buffer, args, sizeof(buffer) - 1);
    buffer[sizeof(buffer) - 1] = '\0';

    char *savePtr = nullptr;
    char *indexText = trimToken(strtok_r(buffer, "|", &savePtr));
    char *nameText = trimToken(strtok_r(nullptr, "|", &savePtr));
    char *typeText = trimToken(strtok_r(nullptr, "|", &savePtr));
    char *messageText = trimToken(strtok_r(nullptr, "|", &savePtr));
    char *gpioText = trimToken(strtok_r(nullptr, "|", &savePtr));
    char *durationText = trimToken(strtok_r(nullptr, "|", &savePtr));
    char *multiplierText = trimToken(strtok_r(nullptr, "|", &savePtr));

    if (!indexText || !nameText || !typeText || !messageText || !gpioText || !durationText)
        return false;

    indexOut = atoi(indexText);
    if (!parseTriggerType(typeText, out.type))
        return false;

    copyStringField(out.name, sizeof(out.name), nameText);
    copyStringField(out.message, sizeof(out.message), messageText);
    out.gpio = (uint8_t)atoi(gpioText);
    out.outputDurationMs = (uint32_t)atoi(durationText);
    out.adcMultiplier = multiplierText ? strtof(multiplierText, nullptr) : 1.0f;
    out.adcAtten = ADC_ATTEN_DB_12;
    out.enabled = true;
    return true;
}

bool DmTriggerModule::handleConfigCommand(const meshtastic_MeshPacket &mp, const char *text)
{
    if (!text || strncmp(text, kConfigPrefix, sizeof(kConfigPrefix) - 1) != 0)
        return false;
    if (!isAuthorizedConfigSender(mp)) {
        LOG_WARN("DmTrigger: rejecting config from unauthorized 0x%08x", mp.from);
        sendConfigReply(mp, "Not authorized");
        return true;
    }

    const char *cmd = text + sizeof(kConfigPrefix) - 1;
    LOG_INFO("DmTrigger: config cmd='%s' from=0x%08x count=%u", cmd, mp.from, triggerCount);
    if (strncmp(cmd, "list", 4) == 0) {
        char reply[220];
        int offset = snprintf(reply, sizeof(reply), "Triggers (%u/%u)", triggerCount, kMaxTriggers);
        for (uint8_t i = 0; i < triggerCount; i++) {
            const Trigger &t = triggers[i];
            offset += snprintf(reply + offset, sizeof(reply) - offset, "\n%u:%s:%s:%s:gpio%u:%ums", i, t.name,
                               triggerTypeName(t.type), t.message, t.gpio, (unsigned)t.outputDurationMs);
            if (offset >= (int)sizeof(reply) - 1)
                break;
        }
        sendConfigReply(mp, reply);
        return true;
    }

    if (strncmp(cmd, "clear", 5) == 0) {
        clearTriggers();
        sendConfigReply(mp, "Triggers cleared");
        return true;
    }

    if (strncmp(cmd, "del|", 4) == 0) {
        const int index = atoi(cmd + 4);
        if (index < 0 || !removeTrigger((uint8_t)index))
            sendConfigReply(mp, "Delete failed");
        else
            sendConfigReply(mp, "Trigger removed");
        return true;
    }

    if (strncmp(cmd, "set|", 4) == 0) {
        Trigger trigger{};
        int index = -1;
        if (!parseSetCommand(cmd + 4, trigger, index)) {
            sendConfigReply(mp, "Set failed: bad format");
            return true;
        }
        if (!isGpioAllowed(trigger.gpio, trigger.type)) {
            sendConfigReply(mp, "Set failed: gpio not allowed");
            return true;
        }
        if (index < 0 && triggerCount >= kMaxTriggers) {
            sendConfigReply(mp, "Set failed: table full");
            return true;
        }

        bool ok = false;
        if (index < 0)
            ok = appendTrigger(trigger);
        else
            ok = setTrigger((uint8_t)index, trigger);

        LOG_INFO("DmTrigger: set index=%d msg='%s' gpio=%u ok=%u count=%u", index, trigger.message, trigger.gpio, ok,
                 triggerCount);
        sendConfigReply(mp, ok ? "Trigger saved" : "Set failed");
        return true;
    }

    sendConfigReply(mp, "Unknown !dmtrigger command");
    return true;
}

float DmTriggerModule::readAnalogVolts(const Trigger &trigger) const
{
    int avgMv = 0;
#ifdef ARCH_ESP32
    adc_channel_t channel;
    if (!gpioToAdc1Channel(trigger.gpio, &channel)) {
        LOG_ERROR("DmTrigger: GPIO %u is not ADC-capable", trigger.gpio);
        return 0.0f;
    }

    const adc_atten_t atten = trigger.adcAtten ? (adc_atten_t)trigger.adcAtten : ADC_ATTEN_DB_12;
    if (!esp32ReadAdc1ChannelMillivolts(channel, atten, &avgMv)) {
        LOG_ERROR("DmTrigger: ADC read failed on GPIO %u", trigger.gpio);
        return 0.0f;
    }
#else
    uint32_t sum = 0;
    constexpr int samples = 8;
    for (int i = 0; i < samples; i++)
        sum += analogRead(trigger.gpio);
    avgMv = (sum / samples) * 3300 / 4095;
#endif

    return (avgMv / 1000.0f) * trigger.adcMultiplier;
}

void DmTriggerModule::pulseOutput(uint8_t gpio, uint32_t durationMs)
{
    // Keep relays energized long enough to actuate; clamp absurd values.
    if (durationMs == 0)
        durationMs = 1000;
    if (durationMs > 600000)
        durationMs = 600000;

    pinMode(gpio, OUTPUT);
    digitalWrite(gpio, HIGH);

    const uint32_t startMs = millis();
    for (ActiveOutput &slot : activeOutputs) {
        if (slot.gpio == 0 || slot.gpio == gpio) {
            slot.gpio = gpio;
            // Store start; runOnce uses duration via offAtMs = start + duration (wrap-safe compare).
            slot.offAtMs = startMs + durationMs;
            if (slot.offAtMs == 0)
                slot.offAtMs = 1; // 0 means empty slot
            break;
        }
    }
    LOG_INFO("DmTrigger: GPIO %u HIGH for %u ms", gpio, (unsigned)durationMs);
    enabled = true;
    setIntervalFromNow(50);
}

void DmTriggerModule::replyAnalog(const meshtastic_MeshPacket &mp, const Trigger &trigger)
{
    const uint8_t triggerIndex = (uint8_t)(&trigger - triggers);
    const uint32_t now = millis();
    if (mp.from == lastAnalogReplyFrom[triggerIndex] && (now - lastAnalogReplyMs[triggerIndex]) < kAnalogReplyCooldownMs)
        return;

    const float volts = readAnalogVolts(trigger);
    char reply[64];
    snprintf(reply, sizeof(reply), "%s: %.3fV", trigger.name[0] ? trigger.name : "Voltage", volts);
    LOG_INFO("DmTrigger: %s replying %.3fV to 0x%08x", trigger.name, volts, mp.from);
    sendDm(mp, reply);

    lastAnalogReplyMs[triggerIndex] = now;
    lastAnalogReplyFrom[triggerIndex] = mp.from;
}

void DmTriggerModule::handleTriggerAction(const meshtastic_MeshPacket &mp, const Trigger &trigger)
{
    if (trigger.type == TriggerType::Output) {
        LOG_INFO("DmTrigger: output '%s' on GPIO %u", trigger.message, trigger.gpio);
        pulseOutput(trigger.gpio, trigger.outputDurationMs);
        return;
    }
    replyAnalog(mp, trigger);
}

ProcessMessage DmTriggerModule::handleReceived(const meshtastic_MeshPacket &mp)
{
    // Direct messages only — ignore channel broadcasts so everyone else does not trigger us.
    if (isBroadcast(mp.to) || !isToUs(&mp))
        return ProcessMessage::CONTINUE;

    auto &payload = mp.decoded;
    if (payload.payload.size == 0)
        return ProcessMessage::CONTINUE;

    char text[kMessageLen + 16];
    size_t len = payload.payload.size;
    if (len >= sizeof(text))
        len = sizeof(text) - 1;
    memcpy(text, payload.payload.bytes, len);
    text[len] = '\0';

    // Trim trailing CR/LF/spaces that some clients append to text messages.
    while (len > 0 && (text[len - 1] == '\n' || text[len - 1] == '\r' || text[len - 1] == ' '))
        text[--len] = '\0';
    // Trim leading spaces/tabs.
    size_t start = 0;
    while (start < len && (text[start] == ' ' || text[start] == '\t'))
        start++;
    if (start > 0) {
        memmove(text, text + start, len - start + 1);
        len -= start;
    }

    if (strncmp(text, kConfigPrefix, sizeof(kConfigPrefix) - 1) == 0) {
        handleConfigCommand(mp, text);
        return ProcessMessage::CONTINUE;
    }

    LOG_INFO("DmTrigger: DM from=0x%08x text='%s' (len=%u) checking %u trigger(s)", mp.from, text, (unsigned)len, triggerCount);

    for (uint8_t i = 0; i < triggerCount; i++) {
        const Trigger &trigger = triggers[i];
        if (!trigger.enabled || trigger.message[0] == '\0')
            continue;

        const size_t queryLen = strlen(trigger.message);
        if (len != queryLen || strncasecmp(text, trigger.message, queryLen) != 0)
            continue;

        LOG_INFO("DmTrigger: matched '%s' type=%u gpio=%u from=0x%08x", trigger.message, (unsigned)trigger.type, trigger.gpio,
                 mp.from);
        handleTriggerAction(mp, trigger);
        return ProcessMessage::CONTINUE;
    }

    if (triggerCount == 0)
        LOG_WARN("DmTrigger: no triggers configured — use !dmtrigger:set or the web UI (then List to verify)");
    else
        LOG_INFO("DmTrigger: no match for '%s'", text);

    return ProcessMessage::CONTINUE;
}

int32_t DmTriggerModule::runOnce()
{
    const uint32_t now = millis();
    bool anyActive = false;

    for (ActiveOutput &slot : activeOutputs) {
        if (slot.gpio == 0)
            continue;
        // Unsigned subtract is rollover-safe: fires once (now - start) reaches duration.
        // offAtMs holds start+duration; treat due when (int32_t)(now - offAtMs) >= 0.
        if (slot.offAtMs != 0 && (int32_t)(now - slot.offAtMs) >= 0) {
            digitalWrite(slot.gpio, LOW);
            LOG_INFO("DmTrigger: GPIO %u LOW", slot.gpio);
            slot.gpio = 0;
            slot.offAtMs = 0;
            continue;
        }
        anyActive = true;
    }

    return anyActive ? 50 : INT32_MAX;
}

#endif
