#pragma once

#include "configuration.h"
#if HAS_DM_TRIGGER

#include "SinglePortModule.h"
#include "concurrency/OSThread.h"

class DmTriggerModule : public SinglePortModule, private concurrency::OSThread
{
  public:
    static constexpr uint8_t kMaxTriggers = 8;
    static constexpr uint8_t kMaxTickets = 8;
    static constexpr uint8_t kNameLen = 20;
    static constexpr uint8_t kMessageLen = 40;
    static constexpr uint16_t kRxTextMax = 200;

    enum class TriggerType : uint8_t { Output = 0, AnalogReading = 1 };

    struct Ticket {
        uint8_t paymentHash[32]{};
    };

    struct Trigger {
        bool enabled = false;
        TriggerType type = TriggerType::Output;
        uint8_t gpio = 0;
        uint32_t outputDurationMs = 0;
        float adcMultiplier = 1.0f;
        uint8_t adcAtten = 0;
        uint32_t priceSats = 0;
        char name[kNameLen]{};
        char message[kMessageLen]{};
        Ticket tickets[kMaxTickets]{};
    };

    DmTriggerModule();

    uint8_t getTriggerCount() const { return triggerCount; }
    const Trigger *getTrigger(uint8_t index) const;
    bool setTrigger(uint8_t index, const Trigger &trigger);
    bool appendTrigger(const Trigger &trigger);
    bool removeTrigger(uint8_t index);
    void clearTriggers();

  protected:
    virtual ProcessMessage handleReceived(const meshtastic_MeshPacket &mp) override;
    virtual int32_t runOnce() override;

  private:
    struct ActiveOutput {
        uint8_t gpio = 0;
        uint32_t offAtMs = 0;
    };

    Trigger triggers[kMaxTriggers]{};
    uint8_t triggerCount = 0;
    ActiveOutput activeOutputs[kMaxTriggers]{};
    uint32_t lastAnalogReplyMs[kMaxTriggers]{};
    uint32_t lastAnalogReplyFrom[kMaxTriggers]{};

    void installDefaultTriggers();
    bool loadFromDisk(bool &hadFile);
    void loadFromDisk();
    bool saveToDisk() const;
    bool isAuthorizedConfigSender(const meshtastic_MeshPacket &mp) const;
    bool isGpioAllowed(uint8_t gpio, TriggerType type) const;
    bool handleConfigCommand(const meshtastic_MeshPacket &mp, const char *text);
    void handleTriggerAction(const meshtastic_MeshPacket &mp, const Trigger &trigger);
    void pulseOutput(uint8_t gpio, uint32_t durationMs);
    void replyAnalog(const meshtastic_MeshPacket &mp, const Trigger &trigger);
    float readAnalogVolts(const Trigger &trigger) const;
    void sendDm(const meshtastic_MeshPacket &rx, const char *text);
    void sendConfigReply(const meshtastic_MeshPacket &rx, const char *text);
    bool parseSetCommand(const char *args, Trigger &out, int &indexOut) const;
    uint8_t countTickets(uint8_t index) const;
    bool addTicket(uint8_t index, const uint8_t hash[32]);
    bool removeTicketHash(uint8_t index, const uint8_t hash[32]);
    bool consumePreimage(uint8_t index, const uint8_t preimage[32]);
};

#endif
