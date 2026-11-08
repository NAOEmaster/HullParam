"""HullParam - parametric hull-structure CAD generator (shell + beam).

Modules
  occ        OpenCASCADE helpers          envelope  hull skin / solid
  extract    CAD -> parameters            comsol    .mph parameter import
  params     YAML schema / expansion      build     parameters -> geometry
  check      integrity checks             export    STEP / IGES / BREP / tables
  viewer     tessellation for the UI      app       local web application
"""
__version__ = "1.1.0"
