#pragma once

#ifdef HELTEC_V3

#include "SinglePortModule.h"

class VoltageQueryModule : public SinglePortModule
{
    uint32_t lastReplyMs = 0;
    uint32_t lastReplyFrom = 0;

  public:
    VoltageQueryModule();

  protected:
    virtual ProcessMessage handleReceived(const meshtastic_MeshPacket &mp) override;

  private:
    float readPinVoltage();
    void sendDm(const meshtastic_MeshPacket &rx, const char *text);
};

#endif
