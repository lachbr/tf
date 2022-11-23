"""ResponseClassRegistry module: contains the ResponseClassRegistry class."""

from . import EngineerResponses
from . import SoldierResponses
from . import ScoutResponses
from . import PyroResponses
from . import DemoResponses
from tf.player.TFClass import Class

ResponseClasses = {
  Class.Engineer: EngineerResponses,
  Class.Soldier: SoldierResponses,
  Class.Scout: ScoutResponses,
  Class.Pyro: PyroResponses,
  Class.Demo: DemoResponses
}

def reload():
    import importlib
    for mod in ResponseClasses.values():
        importlib.reload(mod)
