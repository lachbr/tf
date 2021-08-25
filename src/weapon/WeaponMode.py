from enum import IntEnum

class TFWeaponMode(IntEnum):
    Primary = 0
    Secondary = 1

class TFReloadMode(IntEnum):
    Start = 0
    Reloading = 1
    ReloadingContinue = 2
    Finish = 3

class TFWeaponType(IntEnum):
    Primary = 0
    Primary2 = 1
    Secondary = 2
    Secondary2 = 3
    Melee = 4
    Melee_AllClass = 5
    PDA = 6
    Item1 = 7
    Item2 = 8

class TFProjectileType(IntEnum):
    Invalid = -1
    Bullet = 0
    Rocket = 1
    Pipebomb = 2
    PipebombRemote = 3
    Syringe = 4
