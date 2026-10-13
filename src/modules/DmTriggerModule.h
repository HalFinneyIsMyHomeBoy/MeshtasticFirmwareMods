#pragma once

#include "configuration.h"
#if HAS_DM_TRIGGER

#include "SinglePortModule.h"
#include "concurrency/OSThread.h"

class DmTriggerModule : public SinglePortModule, private concurrency::OSThread
{
  public:
    static constexpr uint8_t kMaxTriggers = 8;
    static constexpr uint8_t kNameLen = 20;
    // One Meshtastic text payload (233) plus a trailing NUL. Match text and reply text each use this.
    static constexpr uint16_t kMessageLen = 234;

    enum class TriggerType : uint8_t { Output = 0, AnalogReading = 1, Reply = 2 };

    struct Trigger {
        bool enabled = false;
        TriggerType type = TriggerType::Output;
        uint8_t gpio = 0;
        uint32_t outputDurationMs = 0;
        float adcMultiplier = 1.0f;
        uint8_t adcAtten = 0;
        char name[kNameLen]{};
        char message[kMessageLen]{};
        char reply[kMessageLen]{};
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
    uint32_t lastMissReplyMs = 0;
    uint32_t lastMissReplyFrom = 0;
    char wrongGuessReply[kMessageLen]{};

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
    void replyWithText(const meshtastic_MeshPacket &mp, const Trigger &trigger);
    float readAnalogVolts(const Trigger &trigger) const;
    struct PendingEdit {
        bool open = false;
        int index = -1;
        Trigger draft{};
        uint16_t messageLen = 0;
        uint16_t replyLen = 0;
    };

    PendingEdit pending{};

    void setWrongGuessReply(const char *text);
    void sendDm(const meshtastic_MeshPacket &rx, const char *text, bool forcePhone = false);
    void sendConfigReply(const meshtastic_MeshPacket &rx, const char *text);
    bool parseSetCommand(const char *args, Trigger &out, int &indexOut) const;
    void openEdit(const meshtastic_MeshPacket &mp, const char *args);
    void putEdit(const meshtastic_MeshPacket &mp, const char *args);
    void commitEdit(const meshtastic_MeshPacket &mp, const char *args);
    void sendTriggerList(const meshtastic_MeshPacket &mp);
    void sendFieldChunks(const meshtastic_MeshPacket &mp, uint8_t index, char kind, const char *text);
};

#endif
