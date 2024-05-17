from .entry import PluginProvider
from .plugin import ExtendedMypyStubs

plugin = PluginProvider(ExtendedMypyStubs, locals())
