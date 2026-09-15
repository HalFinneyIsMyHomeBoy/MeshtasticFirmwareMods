#include "configuration.h"
#if HAS_DM_TRIGGER

#include "DmTriggerModule.h"
#include "FSCommon.h"
#include "MeshService.h"
#include "NodeDB.h"
#include "SPILock.h"
#include "SafeFile.h"
#include "concurrency/LockGuard.h"
#include "main.h"
#include "power.h"
#include <Arduino.h>
#include <SHA256.h>
#include <ctype.h>
#include <string.h>

#ifdef ARCH_ESP32
#include <soc/adc_channel.h>
#endif

namespace
{
constexpr uint32_t kStateMagic = 0x444D5452; // 'DMTR'
constexpr uint8_t kStateVersion = 2;
constexpr uint8_t kStateVersionV1 = 1;
constexpr const char *kStateFile = "/prefs/dm_triggers.bin";
constexpr uint32_t kAnalogReplyCooldownMs = 2000;
constexpr char kConfigPrefix[] = "!dmtrigger:";

#pragma pack(push, 1)
struct PersistedTriggerV1 {
    uint8_t enabled;
    uint8_t type;
    uint8_t gpio;
    uint8_t adcAtten;
    uint32_t outputDurationMs;
    float adcMultiplier;
    char name[DmTriggerModule::kNameLen];
    char message[DmTriggerModule::kMessageLen];
};

struct PersistedStateV1 {
    uint32_t magic;
    uint8_t version;
    uint8_t count;
    uint8_t reserved[2];
    PersistedTriggerV1 triggers[DmTriggerModule::kMaxTriggers];
};

struct PersistedTrigger {
    uint8_t enabled;
    uint8_t type;
    uint8_t gpio;
    uint8_t adcAtten;
    uint32_t outputDurationMs;
    float adcMultiplier;
    char name[DmTriggerModule::kNameLen];
    char message[DmTriggerModule::kMessageLen];
    uint32_t priceSats;
    uint8_t tickets[DmTriggerModule::kMaxTickets][32];
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

static bool reservedPin(uint8_t gpio, int pin)
{
    return pin >= 0 && gpio == static_cast<uint8_t>(pin);
}

static bool isReservedGpio(uint8_t gpio)
{
#ifdef BUTTON_PIN
    if (reservedPin(gpio, BUTTON_PIN))
        return true;
#endif
#ifdef ADC_CTRL
    if (reservedPin(gpio, ADC_CTRL))
        return true;
#endif
#ifdef VEXT_ENABLE
    if (reservedPin(gpio, VEXT_ENABLE))
        return true;
#endif
#ifdef LED_PIN
    if (reservedPin(gpio, LED_PIN))
        return true;
#endif
#ifdef LED_POWER
    if (reservedPin(gpio, LED_POWER))
        return true;
#endif
#ifdef RESET_OLED
    if (reservedPin(gpio, RESET_OLED))
        return true;
#endif
#ifdef I2C_SDA
    if (reservedPin(gpio, I2C_SDA))
        return true;
#endif
#ifdef I2C_SCL
    if (reservedPin(gpio, I2C_SCL))
        return true;
#endif
#ifdef LORA_CS
    if (reservedPin(gpio, LORA_CS))
        return true;
#endif
#ifdef LORA_SCK
    if (reservedPin(gpio, LORA_SCK))
        return true;
#endif
#ifdef LORA_MISO
    if (reservedPin(gpio, LORA_MISO))
        return true;
#endif
#ifdef LORA_MOSI
    if (reservedPin(gpio, LORA_MOSI))
        return true;
#endif
#ifdef LORA_RESET
    if (reservedPin(gpio, LORA_RESET))
        return true;
#endif
#ifdef LORA_DIO0
    if (reservedPin(gpio, LORA_DIO0))
        return true;
#endif
#ifdef LORA_DIO1
    if (reservedPin(gpio, LORA_DIO1))
        return true;
#endif
#ifdef LORA_DIO2
    if (reservedPin(gpio, LORA_DIO2))
        return true;
#endif
#ifdef LORA_PA_POWER
    if (reservedPin(gpio, LORA_PA_POWER))
        return true;
#endif
#ifdef LORA_GC1109_PA_EN
    if (reservedPin(gpio, LORA_GC1109_PA_EN))
        return true;
#endif
#ifdef LORA_GC1109_PA_TX_EN
    if (reservedPin(gpio, LORA_GC1109_PA_TX_EN))
        return true;
#endif
#ifdef LORA_KCT8103L_PA_CSD
    if (reservedPin(gpio, LORA_KCT8103L_PA_CSD))
        return true;
#endif
#ifdef LORA_KCT8103L_PA_CTX
    if (reservedPin(gpio, LORA_KCT8103L_PA_CTX))
        return true;
#endif
#ifdef PIN_GPS_RESET
    if (reservedPin(gpio, PIN_GPS_RESET))
        return true;
#endif
#ifdef PIN_GPS_EN
    if (reservedPin(gpio, PIN_GPS_EN))
        return true;
#endif
#ifdef PIN_GPS_STANDBY
    if (reservedPin(gpio, PIN_GPS_STANDBY))
        return true;
#endif
#ifdef PIN_GPS_PPS
    if (reservedPin(gpio, PIN_GPS_PPS))
        return true;
#endif
#ifdef GPS_TX_PIN
    if (reservedPin(gpio, GPS_TX_PIN))
        return true;
#endif
#ifdef GPS_RX_PIN
    if (reservedPin(gpio, GPS_RX_PIN))
        return true;
#endif
#ifdef PIN_BUZZER
    if (reservedPin(gpio, PIN_BUZZER))
        return true;
#endif
#ifdef LGFX_PIN_SCK
    if (reservedPin(gpio, LGFX_PIN_SCK))
        return true;
#endif
#ifdef LGFX_PIN_MOSI
    if (reservedPin(gpio, LGFX_PIN_MOSI))
        return true;
#endif
#ifdef LGFX_PIN_DC
    if (reservedPin(gpio, LGFX_PIN_DC))
        return true;
#endif
#ifdef LGFX_PIN_CS
    if (reservedPin(gpio, LGFX_PIN_CS))
        return true;
#endif
#ifdef LGFX_PIN_BL
    if (reservedPin(gpio, LGFX_PIN_BL))
        return true;
#endif
#ifdef LGFX_PIN_RST
    if (reservedPin(gpio, LGFX_PIN_RST))
        return true;
#endif
#ifdef TOUCH_RST_PIN
    if (reservedPin(gpio, TOUCH_RST_PIN))
        return true;
#endif
#ifdef DM_TRIGGER_RESERVED_PINS
    static const uint8_t extra[] = {DM_TRIGGER_RESERVED_PINS};
    for (uint8_t pin : extra) {
        if (gpio == pin)
            return true;
    }
#endif
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

static int hexNibble(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static bool parseHex32(const char *hex, uint8_t out[32])
{
    if (!hex || !out)
        return false;
    for (int i = 0; i < 32; i++) {
        const int hi = hexNibble(hex[i * 2]);
        const int lo = hexNibble(hex[i * 2 + 1]);
        if (hi < 0 || lo < 0)
            return false;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return hex[64] == '\0';
}

static bool isAllZero(const uint8_t *bytes, size_t len)
{
    uint8_t acc = 0;
    for (size_t i = 0; i < len; i++)
        acc |= bytes[i];
    return acc == 0;
}

static bool hashesEqual(const uint8_t a[32], const uint8_t b[32])
{
    uint8_t diff = 0;
    for (int i = 0; i < 32; i++)
        diff |= (uint8_t)(a[i] ^ b[i]);
    return diff == 0;
}

static void sha256Bytes(const uint8_t *data, size_t len, uint8_t out[32])
{
    SHA256 sha;
    sha.reset();
    sha.update(data, len);
    sha.finalize(out, 32);
}

static void copyTriggerCore(DmTriggerModule::Trigger &dst, uint8_t enabled, uint8_t type, uint8_t gpio, uint8_t adcAtten,
                            uint32_t outputDurationMs, float adcMultiplier, const char *name, const char *message)
{
    dst = {};
    dst.enabled = enabled != 0;
    dst.type = (type == (uint8_t)DmTriggerModule::TriggerType::AnalogReading) ? DmTriggerModule::TriggerType::AnalogReading
                                                                              : DmTriggerModule::TriggerType::Output;
    dst.gpio = gpio;
    dst.outputDurationMs = outputDurationMs;
    dst.adcMultiplier = adcMultiplier;
    dst.adcAtten = adcAtten;
    copyStringField(dst.name, sizeof(dst.name), name);
    copyStringField(dst.message, sizeof(dst.message), message);
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

    Trigger preserved{};
    const bool preservePay = index < triggerCount;
    if (preservePay)
        preserved = triggers[index];

    triggers[index] = trigger;
    triggers[index].enabled = true;
    if (preservePay) {
        triggers[index].priceSats = preserved.priceSats;
        memcpy(triggers[index].tickets, preserved.tickets, sizeof(preserved.tickets));
    }
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
#ifdef GPIO_TRIGGER_MS
    output.outputDurationMs = GPIO_TRIGGER_MS;
#else
    output.outputDurationMs = 10000;
#endif
    copyStringField(output.name, sizeof(output.name), "GPIO Output");
#ifdef GPIO_TRIGGER_MESSAGE
    copyStringField(output.message, sizeof(output.message), GPIO_TRIGGER_MESSAGE);
#else
    copyStringField(output.message, sizeof(output.message), "Open_Seseme");
#endif
    if (isGpioAllowed(output.gpio, output.type))
        appendTrigger(output);
    else
        LOG_WARN("DmTrigger: skipping default output GPIO %u (reserved on this board)", output.gpio);
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
#ifdef VOLTAGE_QUERY_MESSAGE
    copyStringField(analog.message, sizeof(analog.message), VOLTAGE_QUERY_MESSAGE);
#else
    copyStringField(analog.message, sizeof(analog.message), "Get_Reading");
#endif
    if (isGpioAllowed(analog.gpio, analog.type))
        appendTrigger(analog);
    else
        LOG_WARN("DmTrigger: skipping default analog GPIO %u (reserved on this board)", analog.gpio);
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

    uint8_t raw[sizeof(PersistedState)]{};
    const size_t got = file.read(raw, sizeof(raw));
    file.close();
    if (got < 8) {
        LOG_WARN("DmTrigger: prefs too small (%u)", (unsigned)got);
        return false;
    }

    uint32_t magic = 0;
    memcpy(&magic, raw, sizeof(magic));
    const uint8_t version = raw[4];
    const uint8_t count = raw[5];
    if (magic != kStateMagic || count > kMaxTriggers) {
        LOG_WARN("DmTrigger: invalid prefs (got=%u magic=0x%08x ver=%u count=%u)", (unsigned)got, magic, version, count);
        return false;
    }

    triggerCount = 0;
    memset(triggers, 0, sizeof(triggers));

    if (version == kStateVersionV1) {
        if (got < sizeof(PersistedStateV1)) {
            LOG_WARN("DmTrigger: truncated v1 prefs");
            return false;
        }
        PersistedStateV1 state{};
        memcpy(&state, raw, sizeof(state));
        for (uint8_t i = 0; i < state.count; i++) {
            const PersistedTriggerV1 &src = state.triggers[i];
            copyTriggerCore(triggers[i], src.enabled, src.type, src.gpio, src.adcAtten, src.outputDurationMs, src.adcMultiplier,
                            src.name, src.message);
            triggerCount++;
        }
        LOG_INFO("DmTrigger: migrated %u trigger(s) from v1 prefs", triggerCount);
        return true;
    }

    if (version != kStateVersion || got != sizeof(PersistedState)) {
        LOG_WARN("DmTrigger: invalid prefs (got=%u expect=%u magic=0x%08x ver=%u count=%u)", (unsigned)got,
                 (unsigned)sizeof(PersistedState), magic, version, count);
        return false;
    }

    PersistedState state{};
    memcpy(&state, raw, sizeof(state));
    for (uint8_t i = 0; i < state.count; i++) {
        const PersistedTrigger &src = state.triggers[i];
        copyTriggerCore(triggers[i], src.enabled, src.type, src.gpio, src.adcAtten, src.outputDurationMs, src.adcMultiplier,
                        src.name, src.message);
        triggers[i].priceSats = src.priceSats;
        memcpy(triggers[i].tickets, src.tickets, sizeof(src.tickets));
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
        dst.priceSats = src.priceSats;
        copyStringField(dst.name, sizeof(dst.name), src.name);
        copyStringField(dst.message, sizeof(dst.message), src.message);
        memcpy(dst.tickets, src.tickets, sizeof(src.tickets));
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
    if (isReservedGpio(gpio))
        return false;
#ifdef BATTERY_PIN
    if (type == TriggerType::Output && gpio == BATTERY_PIN)
        return false;
#endif
#ifdef ARCH_ESP32
    if (type == TriggerType::AnalogReading) {
        adc_channel_t channel;
        if (!gpioToAdc1Channel(gpio, &channel))
            return false;
    }
#endif
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

uint8_t DmTriggerModule::countTickets(uint8_t index) const
{
    if (index >= triggerCount)
        return 0;
    uint8_t n = 0;
    for (const Ticket &t : triggers[index].tickets) {
        if (!isAllZero(t.paymentHash, 32))
            n++;
    }
    return n;
}

bool DmTriggerModule::addTicket(uint8_t index, const uint8_t hash[32])
{
    if (index >= triggerCount || !hash || isAllZero(hash, 32))
        return false;
    int empty = -1;
    for (uint8_t i = 0; i < kMaxTickets; i++) {
        if (isAllZero(triggers[index].tickets[i].paymentHash, 32)) {
            if (empty < 0)
                empty = i;
            continue;
        }
        if (hashesEqual(triggers[index].tickets[i].paymentHash, hash))
            return false;
    }
    if (empty < 0)
        return false;
    memcpy(triggers[index].tickets[empty].paymentHash, hash, 32);
    return saveToDisk();
}

bool DmTriggerModule::removeTicketHash(uint8_t index, const uint8_t hash[32])
{
    if (index >= triggerCount || !hash)
        return false;
    for (Ticket &t : triggers[index].tickets) {
        if (hashesEqual(t.paymentHash, hash)) {
            memset(t.paymentHash, 0, 32);
            return saveToDisk();
        }
    }
    return false;
}

bool DmTriggerModule::consumePreimage(uint8_t index, const uint8_t preimage[32])
{
    if (index >= triggerCount || !preimage || isAllZero(preimage, 32))
        return false;
    uint8_t digest[32];
    sha256Bytes(preimage, 32, digest);
    for (Ticket &t : triggers[index].tickets) {
        if (isAllZero(t.paymentHash, 32))
            continue;
        if (!hashesEqual(t.paymentHash, digest))
            continue;
        memset(t.paymentHash, 0, 32);
        if (!saveToDisk())
            return false;
        LOG_INFO("DmTrigger: burned ticket on '%s'", triggers[index].message);
        return true;
    }
    return false;
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
            offset += snprintf(reply + offset, sizeof(reply) - offset, "\n%u:%s:%s:%s:gpio%u:%ums:price=%u:tickets=%u", i, t.name,
                               triggerTypeName(t.type), t.message, t.gpio, (unsigned)t.outputDurationMs, (unsigned)t.priceSats,
                               countTickets(i));
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

    if (strncmp(cmd, "price|", 6) == 0) {
        char buffer[48];
        strncpy(buffer, cmd + 6, sizeof(buffer) - 1);
        buffer[sizeof(buffer) - 1] = '\0';
        char *savePtr = nullptr;
        char *indexText = trimToken(strtok_r(buffer, "|", &savePtr));
        char *satsText = trimToken(strtok_r(nullptr, "|", &savePtr));
        if (!indexText || !satsText) {
            sendConfigReply(mp, "Price failed: bad format");
            return true;
        }
        const int index = atoi(indexText);
        if (index < 0 || (uint8_t)index >= triggerCount) {
            sendConfigReply(mp, "Price failed: bad index");
            return true;
        }
        triggers[index].priceSats = (uint32_t)atoi(satsText);
        sendConfigReply(mp, saveToDisk() ? "Price saved" : "Price failed");
        return true;
    }

    if (strncmp(cmd, "ticket|", 7) == 0) {
        char buffer[96];
        strncpy(buffer, cmd + 7, sizeof(buffer) - 1);
        buffer[sizeof(buffer) - 1] = '\0';
        char *savePtr = nullptr;
        char *indexText = trimToken(strtok_r(buffer, "|", &savePtr));
        char *hashText = trimToken(strtok_r(nullptr, "|", &savePtr));
        uint8_t hash[32];
        if (!indexText || !hashText || !parseHex32(hashText, hash)) {
            sendConfigReply(mp, "Ticket failed: bad format");
            return true;
        }
        const int index = atoi(indexText);
        if (index < 0 || !addTicket((uint8_t)index, hash))
            sendConfigReply(mp, "Ticket failed");
        else
            sendConfigReply(mp, "Ticket stored");
        return true;
    }

    if (strncmp(cmd, "unticket|", 9) == 0) {
        char buffer[96];
        strncpy(buffer, cmd + 9, sizeof(buffer) - 1);
        buffer[sizeof(buffer) - 1] = '\0';
        char *savePtr = nullptr;
        char *indexText = trimToken(strtok_r(buffer, "|", &savePtr));
        char *hashText = trimToken(strtok_r(nullptr, "|", &savePtr));
        uint8_t hash[32];
        if (!indexText || !hashText || !parseHex32(hashText, hash)) {
            sendConfigReply(mp, "Unticket failed: bad format");
            return true;
        }
        const int index = atoi(indexText);
        if (index < 0 || !removeTicketHash((uint8_t)index, hash))
            sendConfigReply(mp, "Unticket failed");
        else
            sendConfigReply(mp, "Ticket removed");
        return true;
    }

    if (strncmp(cmd, "tickets|", 8) == 0) {
        const int index = atoi(cmd + 8);
        if (index < 0 || (uint8_t)index >= triggerCount) {
            sendConfigReply(mp, "Tickets failed: bad index");
            return true;
        }
        char reply[220];
        int offset = snprintf(reply, sizeof(reply), "Tickets %u unused=%u price=%u", (unsigned)index, countTickets((uint8_t)index),
                              (unsigned)triggers[index].priceSats);
        for (uint8_t i = 0; i < kMaxTickets; i++) {
            const uint8_t *h = triggers[index].tickets[i].paymentHash;
            if (isAllZero(h, 32))
                continue;
            offset += snprintf(reply + offset, sizeof(reply) - offset, "\n%u:%02x%02x%02x%02x…", i, h[0], h[1], h[2], h[3]);
            if (offset >= (int)sizeof(reply) - 1)
                break;
        }
        sendConfigReply(mp, reply);
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

    char text[kRxTextMax + 1];
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

    const bool usbOrSelf = (mp.from == 0) || isFromUs(&mp);

    for (uint8_t i = 0; i < triggerCount; i++) {
        Trigger &trigger = triggers[i];
        if (!trigger.enabled || trigger.message[0] == '\0')
            continue;

        const size_t queryLen = strlen(trigger.message);
        if (len < queryLen || strncasecmp(text, trigger.message, queryLen) != 0)
            continue;

        const bool exact = (len == queryLen);
        const bool ticketForm = (len == queryLen + 1 + 64 && text[queryLen] == ':');

        if (trigger.priceSats == 0 || usbOrSelf) {
            if (exact) {
                LOG_INFO("DmTrigger: matched '%s' type=%u gpio=%u from=0x%08x", trigger.message, (unsigned)trigger.type,
                         trigger.gpio, mp.from);
                handleTriggerAction(mp, trigger);
                return ProcessMessage::CONTINUE;
            }
            if (usbOrSelf && ticketForm) {
                uint8_t preimage[32];
                char hex[65];
                memcpy(hex, text + queryLen + 1, 64);
                hex[64] = '\0';
                if (parseHex32(hex, preimage) && consumePreimage(i, preimage)) {
                    handleTriggerAction(mp, trigger);
                    return ProcessMessage::CONTINUE;
                }
            }
            continue;
        }

        if (exact) {
            LOG_INFO("DmTrigger: ignoring unpaid phrase for priced trigger '%s'", trigger.message);
            continue;
        }
        if (!ticketForm)
            continue;

        uint8_t preimage[32];
        char hex[65];
        memcpy(hex, text + queryLen + 1, 64);
        hex[64] = '\0';
        if (!parseHex32(hex, preimage) || !consumePreimage(i, preimage)) {
            LOG_WARN("DmTrigger: ticket not found (missing or already spent) for '%s'", trigger.message);
            continue;
        }

        LOG_INFO("DmTrigger: paid match '%s' type=%u gpio=%u from=0x%08x", trigger.message, (unsigned)trigger.type, trigger.gpio,
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
