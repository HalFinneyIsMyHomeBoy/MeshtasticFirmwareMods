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
#include "Power.h"
#include "mesh/Throttle.h"
#include "mesh/generated/meshtastic/mesh.pb.h"
#include <Arduino.h>
#include <ctype.h>
#include <string.h>

#ifdef ARCH_ESP32
#include <soc/adc_channel.h>
#endif

namespace
{
constexpr uint32_t kStateMagic = 0x444D5452; // 'DMTR'
constexpr uint8_t kStateVersion = 4;
// "!dmtrigger:miss|" plus this text must fit in one 233-byte payload.
constexpr size_t kWrongGuessMax = 216;
constexpr size_t kLegacyMessageLen = 40; // v1/v2 on-disk message and reply size
static_assert(DmTriggerModule::kMessageLen == meshtastic_Constants_DATA_PAYLOAD_LEN + 1,
              "match and reply text must hold one Meshtastic payload plus NUL");
constexpr const char *kStateFile = "/prefs/dm_triggers.bin";
constexpr uint32_t kAnalogReplyCooldownMs = 2000;
constexpr char kConfigPrefix[] = "!dmtrigger:";
constexpr char kWrongGuessReply[] = "nah ah ah, you didn't say the magic words!";

#pragma pack(push, 1)
struct PersistedHeader {
    uint32_t magic;
    uint8_t version;
    uint8_t count;
    uint8_t reserved[2];
};

struct PersistedTriggerV1 {
    uint8_t enabled;
    uint8_t type;
    uint8_t gpio;
    uint8_t adcAtten;
    uint32_t outputDurationMs;
    float adcMultiplier;
    char name[DmTriggerModule::kNameLen];
    char message[kLegacyMessageLen];
};

struct PersistedTriggerV2 {
    uint8_t enabled;
    uint8_t type;
    uint8_t gpio;
    uint8_t adcAtten;
    uint32_t outputDurationMs;
    float adcMultiplier;
    char name[DmTriggerModule::kNameLen];
    char message[kLegacyMessageLen];
    char reply[kLegacyMessageLen];
};

struct PersistedTriggerV3 {
    uint8_t enabled;
    uint8_t type;
    uint8_t gpio;
    uint8_t adcAtten;
    uint32_t outputDurationMs;
    float adcMultiplier;
    char name[DmTriggerModule::kNameLen];
    char message[DmTriggerModule::kMessageLen];
    char reply[DmTriggerModule::kMessageLen];
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
    if (type == DmTriggerModule::TriggerType::AnalogReading)
        return "analog";
    if (type == DmTriggerModule::TriggerType::Reply)
        return "reply";
    return "output";
}

static DmTriggerModule::TriggerType triggerTypeFromStored(uint8_t type)
{
    if (type == (uint8_t)DmTriggerModule::TriggerType::AnalogReading)
        return DmTriggerModule::TriggerType::AnalogReading;
    if (type == (uint8_t)DmTriggerModule::TriggerType::Reply)
        return DmTriggerModule::TriggerType::Reply;
    return DmTriggerModule::TriggerType::Output;
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
    if (strcasecmp(text, "reply") == 0 || strcasecmp(text, "text") == 0) {
        typeOut = DmTriggerModule::TriggerType::Reply;
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

static const char *takeField(const char *s, char *dest, size_t destLen, bool &ok)
{
    ok = false;
    if (!s || !dest || destLen == 0)
        return nullptr;
    const char *pipe = strchr(s, '|');
    const size_t n = pipe ? (size_t)(pipe - s) : strlen(s);
    if (n >= destLen)
        return nullptr;
    memcpy(dest, s, n);
    dest[n] = '\0';
    char *trimmed = trimToken(dest);
    if (trimmed != dest)
        memmove(dest, trimmed, strlen(trimmed) + 1);
    ok = true;
    return pipe ? pipe + 1 : s + strlen(s);
}

static void trimMatchField(char *text, uint16_t &len)
{
    size_t start = 0;
    size_t end = len;
    while (start < end && (text[start] == ' ' || text[start] == '\t'))
        start++;
    while (end > start && (text[end - 1] == '\n' || text[end - 1] == '\r' || text[end - 1] == ' ' || text[end - 1] == '\t'))
        end--;
    const size_t n = end - start;
    if (start > 0 && n > 0)
        memmove(text, text + start, n);
    text[n] = '\0';
    len = (uint16_t)n;
}

static size_t utf8ChunkBytes(const char *text, size_t available, size_t room)
{
    size_t n = 0;
    while (n < available && n < room) {
        const auto lead = static_cast<unsigned char>(text[n]);
        size_t need = 1;
        if ((lead & 0xE0) == 0xC0)
            need = 2;
        else if ((lead & 0xF0) == 0xE0)
            need = 3;
        else if ((lead & 0xF8) == 0xF0)
            need = 4;
        if (n + need > available || n + need > room)
            break;
        n += need;
    }
    if (n == 0 && available > 0 && room > 0)
        n = 1;
    return n;
}

static void fillTrigger(DmTriggerModule::Trigger &dst, uint8_t enabled, uint8_t type, uint8_t gpio, uint8_t adcAtten,
                        uint32_t durationMs, float multiplier, const char *name, const char *message, const char *reply)
{
    dst = {};
    dst.enabled = enabled != 0;
    dst.type = triggerTypeFromStored(type);
    dst.gpio = gpio;
    dst.adcAtten = adcAtten;
    dst.outputDurationMs = durationMs;
    dst.adcMultiplier = multiplier;
    copyStringField(dst.name, sizeof(dst.name), name);
    copyStringField(dst.message, sizeof(dst.message), message);
    copyStringField(dst.reply, sizeof(dst.reply), reply ? reply : "");
}
} // namespace

DmTriggerModule::DmTriggerModule() : SinglePortModule("DmTrigger", meshtastic_PortNum_TEXT_MESSAGE_APP), OSThread("DmTrigger")
{
    setWrongGuessReply(nullptr);
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
    if (trigger.type == TriggerType::Reply) {
        if (trigger.reply[0] == '\0')
            return false;
    } else if (!isGpioAllowed(trigger.gpio, trigger.type)) {
        return false;
    }
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
    pending.open = false;
    return saveToDisk();
}

void DmTriggerModule::clearTriggers()
{
    memset(triggers, 0, sizeof(triggers));
    triggerCount = 0;
    pending.open = false;
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

    PersistedHeader header{};
    size_t got = file.read(reinterpret_cast<uint8_t *>(&header), sizeof(header));
    if (got != sizeof(header) || header.magic != kStateMagic || header.count > kMaxTriggers || header.version < 1 ||
        header.version > kStateVersion) {
        file.close();
        LOG_WARN("DmTrigger: invalid prefs (got=%u magic=0x%08x ver=%u count=%u)", (unsigned)got, header.magic, header.version,
                 header.count);
        return false;
    }

    triggerCount = 0;
    memset(triggers, 0, sizeof(triggers));
    setWrongGuessReply(nullptr);
    bool shortFile = false;
    if (header.version == 1) {
        PersistedTriggerV1 legacy[kMaxTriggers]{};
        got = file.read(reinterpret_cast<uint8_t *>(legacy), sizeof(legacy));
        shortFile = got != sizeof(legacy);
        if (!shortFile) {
            for (uint8_t i = 0; i < header.count; i++)
                fillTrigger(triggers[i], legacy[i].enabled, legacy[i].type, legacy[i].gpio, legacy[i].adcAtten,
                            legacy[i].outputDurationMs, legacy[i].adcMultiplier, legacy[i].name, legacy[i].message, "");
        }
    } else if (header.version == 2) {
        PersistedTriggerV2 legacy[kMaxTriggers]{};
        got = file.read(reinterpret_cast<uint8_t *>(legacy), sizeof(legacy));
        shortFile = got != sizeof(legacy);
        if (!shortFile) {
            for (uint8_t i = 0; i < header.count; i++)
                fillTrigger(triggers[i], legacy[i].enabled, legacy[i].type, legacy[i].gpio, legacy[i].adcAtten,
                            legacy[i].outputDurationMs, legacy[i].adcMultiplier, legacy[i].name, legacy[i].message,
                            legacy[i].reply);
        }
    } else if (header.version == 3 || header.version == 4) {
        for (uint8_t i = 0; i < kMaxTriggers && !shortFile; i++) {
            PersistedTriggerV3 one{};
            got = file.read(reinterpret_cast<uint8_t *>(&one), sizeof(one));
            if (got != sizeof(one)) {
                shortFile = true;
                break;
            }
            if (i < header.count)
                fillTrigger(triggers[i], one.enabled, one.type, one.gpio, one.adcAtten, one.outputDurationMs, one.adcMultiplier,
                            one.name, one.message, one.reply);
        }
        if (!shortFile && header.version == 4) {
            char stored[kMessageLen]{};
            got = file.read(reinterpret_cast<uint8_t *>(stored), sizeof(stored));
            if (got != sizeof(stored))
                shortFile = true;
            else {
                stored[sizeof(stored) - 1] = '\0';
                setWrongGuessReply(stored);
            }
        }
    } else {
        file.close();
        LOG_WARN("DmTrigger: unsupported prefs version %u", header.version);
        return false;
    }
    file.close();
    if (shortFile) {
        LOG_WARN("DmTrigger: short v%u prefs", header.version);
        memset(triggers, 0, sizeof(triggers));
        setWrongGuessReply(nullptr);
        return false;
    }
    triggerCount = header.count;
    LOG_INFO("DmTrigger: restored %u trigger(s) from disk", triggerCount);
    return true;
#else
    return false;
#endif
}

bool DmTriggerModule::saveToDisk() const
{
#ifdef FSCom
    {
        concurrency::LockGuard g(spiLock);
        FSCom.mkdir("/prefs");
    }
    auto file = SafeFile(kStateFile, true);
    PersistedHeader header{};
    header.magic = kStateMagic;
    header.version = kStateVersion;
    header.count = triggerCount;
    size_t written = file.write(reinterpret_cast<const uint8_t *>(&header), sizeof(header));
    for (uint8_t i = 0; i < kMaxTriggers; i++) {
        PersistedTriggerV3 dst{};
        if (i < triggerCount) {
            const Trigger &src = triggers[i];
            dst.enabled = src.enabled ? 1 : 0;
            dst.type = (uint8_t)src.type;
            dst.gpio = src.gpio;
            dst.outputDurationMs = src.outputDurationMs;
            dst.adcMultiplier = src.adcMultiplier;
            dst.adcAtten = src.adcAtten;
            copyStringField(dst.name, sizeof(dst.name), src.name);
            copyStringField(dst.message, sizeof(dst.message), src.message);
            copyStringField(dst.reply, sizeof(dst.reply), src.reply);
        }
        written += file.write(reinterpret_cast<const uint8_t *>(&dst), sizeof(dst));
    }
    char storedGuess[kMessageLen]{};
    copyStringField(storedGuess, sizeof(storedGuess), wrongGuessReply);
    written += file.write(reinterpret_cast<const uint8_t *>(storedGuess), sizeof(storedGuess));
    const size_t expect = sizeof(header) + sizeof(PersistedTriggerV3) * kMaxTriggers + sizeof(storedGuess);
    const bool ok = file.close() && written == expect;
    if (ok)
        LOG_INFO("DmTrigger: saved %u trigger(s) to %s", triggerCount, kStateFile);
    else
        LOG_ERROR("DmTrigger: failed to save prefs (%u of %u bytes)", (unsigned)written, (unsigned)expect);
    return ok;
#else
    return false;
#endif
}

bool DmTriggerModule::isGpioAllowed(uint8_t gpio, TriggerType type) const
{
    if (type == TriggerType::Reply)
        return true;
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

void DmTriggerModule::setWrongGuessReply(const char *text)
{
    if (!text || text[0] == '\0') {
        copyStringField(wrongGuessReply, sizeof(wrongGuessReply), kWrongGuessReply);
        return;
    }

    char cleaned[kMessageLen];
    size_t n = 0;
    for (const char *p = text; *p && n + 1 < sizeof(cleaned); p++) {
        char c = *p;
        if (c == '\n' || c == '\r' || c == '\t')
            c = ' ';
        cleaned[n++] = c;
    }
    cleaned[n] = '\0';
    while (n > 0 && cleaned[n - 1] == ' ')
        cleaned[--n] = '\0';
    if (cleaned[0] == '\0' || n > kWrongGuessMax) {
        copyStringField(wrongGuessReply, sizeof(wrongGuessReply), kWrongGuessReply);
        return;
    }
    copyStringField(wrongGuessReply, sizeof(wrongGuessReply), cleaned);
}

void DmTriggerModule::sendDm(const meshtastic_MeshPacket &rx, const char *text, bool forcePhone)
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
    if (forcePhone || rx.from == 0) {
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
    sendDm(rx, text, isFromUs(&rx));
}

void DmTriggerModule::sendFieldChunks(const meshtastic_MeshPacket &mp, uint8_t index, char kind, const char *text)
{
    const size_t total = text ? strlen(text) : 0;
    size_t offset = 0;
    while (offset < total) {
        char packet[kMessageLen];
        const int headerLen = snprintf(packet, sizeof(packet), "Trigger%c|%u|%u|", kind, index, (unsigned)offset);
        if (headerLen < 0 || (size_t)headerLen >= meshtastic_Constants_DATA_PAYLOAD_LEN)
            return;
        const size_t room = meshtastic_Constants_DATA_PAYLOAD_LEN - (size_t)headerLen;
        const size_t n = utf8ChunkBytes(text + offset, total - offset, room);
        memcpy(packet + headerLen, text + offset, n);
        packet[headerLen + n] = '\0';
        sendConfigReply(mp, packet);
        delay(20);
        offset += n;
    }
}

void DmTriggerModule::sendTriggerList(const meshtastic_MeshPacket &mp)
{
    char line[96];
    snprintf(line, sizeof(line), "Triggers (%u/%u)", triggerCount, kMaxTriggers);
    sendConfigReply(mp, line);
    delay(20);
    for (uint8_t i = 0; i < triggerCount; i++) {
        const Trigger &t = triggers[i];
        snprintf(line, sizeof(line), "Trigger|%u|%s|%s|%u|%u|%.8g", i, t.name, triggerTypeName(t.type), t.gpio,
                 (unsigned)t.outputDurationMs, t.adcMultiplier);
        sendConfigReply(mp, line);
        delay(20);
        sendFieldChunks(mp, i, 'M', t.message);
        sendFieldChunks(mp, i, 'R', t.reply);
    }
    char wrongLine[kMessageLen + 8];
    snprintf(wrongLine, sizeof(wrongLine), "Wrong|%s", wrongGuessReply[0] ? wrongGuessReply : kWrongGuessReply);
    sendConfigReply(mp, wrongLine);
    delay(20);
    sendConfigReply(mp, "Triggers end");
}

void DmTriggerModule::openEdit(const meshtastic_MeshPacket &mp, const char *args)
{
    char indexText[8];
    char nameText[kNameLen];
    char typeText[16];
    char gpioText[8];
    char durationText[12];
    char multiplierText[24];
    bool ok = false;
    const char *rest = takeField(args, indexText, sizeof(indexText), ok);
    if (ok)
        rest = takeField(rest, nameText, sizeof(nameText), ok);
    if (ok)
        rest = takeField(rest, typeText, sizeof(typeText), ok);
    if (ok)
        rest = takeField(rest, gpioText, sizeof(gpioText), ok);
    if (ok)
        rest = takeField(rest, durationText, sizeof(durationText), ok);
    if (ok)
        rest = takeField(rest, multiplierText, sizeof(multiplierText), ok);
    if (!ok) {
        sendConfigReply(mp, "Set failed: bad format");
        return;
    }

    const int index = atoi(indexText);
    TriggerType type;
    if (index < -1 || index >= (int)kMaxTriggers || !parseTriggerType(typeText, type)) {
        sendConfigReply(mp, "Set failed: bad format");
        return;
    }

    pending = {};
    pending.open = true;
    pending.index = index;
    pending.draft.enabled = true;
    pending.draft.type = type;
    copyStringField(pending.draft.name, sizeof(pending.draft.name), nameText);
    pending.draft.gpio = (uint8_t)atoi(gpioText);
    pending.draft.outputDurationMs = (uint32_t)atoi(durationText);
    pending.draft.adcMultiplier = strtof(multiplierText, nullptr);
    pending.draft.adcAtten = ADC_ATTEN_DB_12;
    pending.messageLen = 0;
    pending.replyLen = 0;
    if (type == TriggerType::Reply)
        pending.draft.gpio = 0;
    else if (!isGpioAllowed(pending.draft.gpio, type)) {
        pending.open = false;
        sendConfigReply(mp, "Set failed: gpio not allowed");
        return;
    }
    sendConfigReply(mp, "Opened");
}

void DmTriggerModule::putEdit(const meshtastic_MeshPacket &mp, const char *args)
{
    char indexText[8];
    char fieldText[4];
    char offsetText[8];
    bool ok = false;
    const char *rest = takeField(args, indexText, sizeof(indexText), ok);
    if (ok)
        rest = takeField(rest, fieldText, sizeof(fieldText), ok);
    if (ok)
        rest = takeField(rest, offsetText, sizeof(offsetText), ok);
    if (!ok || !rest) {
        sendConfigReply(mp, "Set failed: bad format");
        return;
    }
    if (!pending.open || atoi(indexText) != pending.index) {
        sendConfigReply(mp, "Set failed: no edit in progress");
        return;
    }

    const bool isMessage = strcmp(fieldText, "m") == 0;
    const bool isReply = strcmp(fieldText, "r") == 0;
    if (!isMessage && !isReply) {
        sendConfigReply(mp, "Set failed: bad format");
        return;
    }

    uint16_t &got = isMessage ? pending.messageLen : pending.replyLen;
    char *dest = isMessage ? pending.draft.message : pending.draft.reply;
    const unsigned offset = (unsigned)strtoul(offsetText, nullptr, 10);
    const size_t n = strlen(rest);
    if (n == 0) {
        sendConfigReply(mp, "Set failed: empty part");
        return;
    }
    // A retry may overlap text already stored. A gap means an earlier part was lost.
    if (offset > got || (size_t)offset + n > meshtastic_Constants_DATA_PAYLOAD_LEN) {
        char fail[64];
        snprintf(fail, sizeof(fail), "Set failed: bad offset %u/%u", offset, (unsigned)got);
        sendConfigReply(mp, fail);
        return;
    }
    memcpy(dest + offset, rest, n);
    const uint16_t end = (uint16_t)(offset + n);
    if (end > got)
        got = end;
    dest[got] = '\0';
    char okMsg[32];
    snprintf(okMsg, sizeof(okMsg), "Part ok|%u", (unsigned)got);
    sendConfigReply(mp, okMsg);
}

void DmTriggerModule::commitEdit(const meshtastic_MeshPacket &mp, const char *args)
{
    char indexText[8];
    bool ok = false;
    takeField(args, indexText, sizeof(indexText), ok);
    if (!ok || !pending.open || atoi(indexText) != pending.index) {
        sendConfigReply(mp, "Set failed: no edit in progress");
        return;
    }

    trimMatchField(pending.draft.message, pending.messageLen);
    if (pending.draft.message[0] == '\0') {
        sendConfigReply(mp, "Set failed: message required");
        return;
    }
    if (pending.draft.type == TriggerType::Reply && pending.draft.reply[0] == '\0') {
        sendConfigReply(mp, "Set failed: reply text required");
        return;
    }
    if (pending.draft.type != TriggerType::Reply && !isGpioAllowed(pending.draft.gpio, pending.draft.type)) {
        sendConfigReply(mp, "Set failed: gpio not allowed");
        return;
    }
    if (pending.index < 0 && triggerCount >= kMaxTriggers) {
        sendConfigReply(mp, "Set failed: table full");
        return;
    }

    const int index = pending.index;
    const bool saved = index < 0 ? appendTrigger(pending.draft) : setTrigger((uint8_t)index, pending.draft);
    pending.open = false;
    LOG_INFO("DmTrigger: commit index=%d msgLen=%u replyLen=%u ok=%u", index, pending.messageLen, pending.replyLen, saved);
    sendConfigReply(mp, saved ? "Trigger saved" : "Set failed");
}

bool DmTriggerModule::parseSetCommand(const char *args, Trigger &out, int &indexOut) const
{
    char buffer[320];
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
    char *replyText = strtok_r(nullptr, "|", &savePtr);
    if (replyText)
        replyText = trimToken(replyText);

    if (!indexText || !nameText || !typeText || !messageText || !gpioText || !durationText)
        return false;

    indexOut = atoi(indexText);
    if (!parseTriggerType(typeText, out.type))
        return false;

    copyStringField(out.name, sizeof(out.name), nameText);
    copyStringField(out.message, sizeof(out.message), messageText);
    if (replyText)
        copyStringField(out.reply, sizeof(out.reply), replyText);
    out.gpio = (uint8_t)atoi(gpioText);
    out.outputDurationMs = (uint32_t)atoi(durationText);
    out.adcMultiplier = multiplierText ? strtof(multiplierText, nullptr) : 1.0f;
    out.adcAtten = ADC_ATTEN_DB_12;
    out.enabled = true;
    if (out.type == TriggerType::Reply)
        out.gpio = 0;
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
        sendTriggerList(mp);
        return true;
    }

    if (strncmp(cmd, "miss|", 5) == 0) {
        const char *body = cmd + 5;
        if (body[0] == '\0' || strlen(body) > kWrongGuessMax) {
            sendConfigReply(mp, "Set failed: wrong guess text");
            return true;
        }
        setWrongGuessReply(body);
        LOG_INFO("DmTrigger: wrong-guess reply set");
        sendConfigReply(mp, saveToDisk() ? "Wrong guess saved" : "Set failed");
        return true;
    }

    if (strncmp(cmd, "open|", 5) == 0) {
        openEdit(mp, cmd + 5);
        return true;
    }

    if (strncmp(cmd, "put|", 4) == 0) {
        putEdit(mp, cmd + 4);
        return true;
    }

    if (strncmp(cmd, "commit|", 7) == 0) {
        commitEdit(mp, cmd + 7);
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
        pending.open = false;
        Trigger trigger{};
        int index = -1;
        if (!parseSetCommand(cmd + 4, trigger, index)) {
            sendConfigReply(mp, "Set failed: bad format");
            return true;
        }
        if (trigger.type == TriggerType::Reply && trigger.reply[0] == '\0') {
            sendConfigReply(mp, "Set failed: reply text required");
            return true;
        }
        if (trigger.type != TriggerType::Reply && !isGpioAllowed(trigger.gpio, trigger.type)) {
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

void DmTriggerModule::replyWithText(const meshtastic_MeshPacket &mp, const Trigger &trigger)
{
    if (trigger.reply[0] == '\0')
        return;

    const uint8_t triggerIndex = (uint8_t)(&trigger - triggers);
    const uint32_t now = millis();
    if (mp.from == lastAnalogReplyFrom[triggerIndex] && (now - lastAnalogReplyMs[triggerIndex]) < kAnalogReplyCooldownMs)
        return;

    LOG_INFO("DmTrigger: reply '%s' -> '%s' to 0x%08x", trigger.message, trigger.reply, mp.from);
    sendDm(mp, trigger.reply);
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
    if (trigger.type == TriggerType::Reply) {
        replyWithText(mp, trigger);
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

    if (strncmp(text, kConfigPrefix, sizeof(kConfigPrefix) - 1) == 0) {
        handleConfigCommand(mp, text);
        return ProcessMessage::CONTINUE;
    }

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

    LOG_INFO("DmTrigger: DM from=0x%08x text='%s' (len=%u) checking %u trigger(s)", mp.from, text, (unsigned)len, triggerCount);

    bool anyEnabled = false;
    for (uint8_t i = 0; i < triggerCount; i++) {
        const Trigger &trigger = triggers[i];
        if (!trigger.enabled || trigger.message[0] == '\0')
            continue;
        anyEnabled = true;

        const size_t queryLen = strlen(trigger.message);
        if (len != queryLen || strncasecmp(text, trigger.message, queryLen) != 0)
            continue;

        LOG_INFO("DmTrigger: matched '%s' type=%u gpio=%u from=0x%08x", trigger.message, (unsigned)trigger.type, trigger.gpio,
                 mp.from);
        handleTriggerAction(mp, trigger);
        return ProcessMessage::CONTINUE;
    }

    if (!anyEnabled) {
        LOG_WARN("DmTrigger: no triggers configured — use !dmtrigger:set or the web UI (then List to verify)");
        return ProcessMessage::CONTINUE;
    }

    if (mp.from == lastMissReplyFrom && lastMissReplyMs != 0 &&
        Throttle::isWithinTimespanMs(lastMissReplyMs, kAnalogReplyCooldownMs)) {
        LOG_INFO("DmTrigger: wrong guess from 0x%08x still in cooldown", mp.from);
        return ProcessMessage::CONTINUE;
    }

    LOG_INFO("DmTrigger: no match for '%s'", text);
    sendDm(mp, wrongGuessReply[0] ? wrongGuessReply : kWrongGuessReply);
    lastMissReplyMs = millis();
    lastMissReplyFrom = mp.from;
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
