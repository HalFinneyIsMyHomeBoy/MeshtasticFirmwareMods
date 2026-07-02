#pragma once

#ifdef HELTEC_V3

#include "SinglePortModule.h"
#include "concurrency/OSThread.h"

class GpioTriggerModule : public SinglePortModule, private concurrency::OSThread
{
    uint32_t offAtMs = 0;

  public:
    GpioTriggerModule();

  protected:
    virtual ProcessMessage handleReceived(const meshtastic_MeshPacket &mp) override;
    virtual int32_t runOnce() override;
};

#endif
