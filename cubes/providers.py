# -*- coding=utf -*-
"""Logical model model providers."""
import jsonschema
import urlparse
import urllib2
import pkgutil
import shutil
import copy
import json
import os
import re

from collections import namedtuple
from .extensions import get_namespace, initialize_namespace
from .logging import get_logger
from .common import IgnoringDictionary, to_label
from .errors import *
from .model import *

__all__ = [
    "read_model_metadata",
    "read_model_metadata_bundle",
    "write_model_metadata_bundle",

    "create_model_provider",
    "ModelProvider",
    "StaticModelProvider",
    "validate_model",

    # FIXME: Depreciated
    "load_model",
    "model_from_path",
    "create_model",
    "merge_models",
]


def create_model_provider(name, metadata):
    """Gets a new instance of a model provider with name `name`."""

    ns = get_namespace("model_providers")
    if not ns:
    # FIXME: depreciated. affected: formatter.py
        ns = initialize_namespace("model_providers", root_class=ModelProvider,
                                  suffix="_model_provider")
        ns["default"] = StaticModelProvider

    try:
        factory = ns[name]
    except KeyError:
        raise CubesError("Unable to find model provider of type '%s'" % name)

    return factory(metadata)

def _json_from_url(url):
    """Opens `resource` either as a file with `open()`or as URL with
    `urllib2.urlopen()`. Returns opened handle. """

    parts = urlparse.urlparse(url)
    if parts.scheme in ('', 'file'):
        handle = open(parts.path)
    else:
        handle = urllib2.urlopen(url)

    try:
        desc = json.load(handle)
    except ValueError as e:
        raise SyntaxError("Syntax error in %s: %s" % (url, e.args))
    finally:
        handle.close()

    return desc


def read_model_metadata(source):
    """Reads a model description from `source` which can be a filename, URL,
    file-like object or a path to a directory. Returns a model description
    dictionary."""

    if isinstance(source, basestring):
        parts = urlparse.urlparse(source)
        if parts.scheme in ('', 'file') and os.path.isdir(parts.path):
            source = parts.path
            return read_model_metadata_bundle(source)
        else:
            return _json_from_url(source)
    else:
        return json.load(source)


def read_model_metadata_bundle(path):
    """Load logical model a directory specified by `path`.  Returns a model
    description dictionary. Model directory bundle has structure:

    * ``model.cubesmodel/``
        * ``model.json``
        * ``dim_*.json``
        * ``cube_*.json``

    The dimensions and cubes lists in the ``model.json`` are concatenated with
    dimensions and cubes from the separate files.
    """

    if not os.path.isdir(path):
        raise ArgumentError("Path '%s' is not a directory.")

    info_path = os.path.join(path, 'model.json')

    if not os.path.exists(info_path):
        raise ModelError('main model info %s does not exist' % info_path)

    model = _json_from_url(info_path)

    # Find model object files and load them

    if not "dimensions" in model:
        model["dimensions"] = []

    if not "cubes" in model:
        model["cubes"] = []

    for dirname, dirnames, filenames in os.walk(path):
        for filename in filenames:
            if os.path.splitext(filename)[1] != '.json':
                continue

            split = re.split('_', filename)
            prefix = split[0]
            obj_path = os.path.join(dirname, filename)

            if prefix in ('dim', 'dimension'):
                desc = _json_from_url(obj_path)
                try:
                    name = desc["name"]
                except KeyError:
                    raise ModelError("Dimension file '%s' has no name key" %
                                                                     obj_path)
                if name in model["dimensions"]:
                    raise ModelError("Dimension '%s' defined multiple times " %
                                        "(in '%s')" % (name, obj_path) )
                model["dimensions"].append(desc)

            elif prefix == 'cube':
                desc = _json_from_url(obj_path)
                try:
                    name = desc["name"]
                except KeyError:
                    raise ModelError("Cube file '%s' has no name key" %
                                                                     obj_path)
                if name in model["cubes"]:
                    raise ModelError("Cube '%s' defined multiple times "
                                        "(in '%s')" % (name, obj_path) )
                model["cubes"].append(desc)

    return model


def write_model_metadata_bundle(path, metadata, replace=False):
    """Writes a model metadata bundle into new directory `target` from
    `metadata`. Directory should not exist."""

    if os.path.exists(path):
        if not os.path.isdir(path):
            raise CubesError("Target exists and is a file, "
                                "can not replace")
        elif not os.path.exists(os.path.join(path, "model.json")):
            raise CubesError("Target is not a model directory, "
                                "can not replace.")
        if replace:
            shutil.rmtree(path)
        else:
            raise CubesError("Target already exists. "
                                "Remove it or force replacement.")

    os.makedirs(path)

    metadata = dict(metadata)

    dimensions = metadata.pop("dimensions", [])
    cubes = metadata.pop("cubes", [])

    for dim in dimensions:
        name = dim["name"]
        filename = os.path.join(path, "dim_%s.json" % name)
        with open(filename, "w") as f:
            json.dump(dim, f, indent=4)

    for cube in cubes:
        name = cube["name"]
        filename = os.path.join(path, "cube_%s.json" % name)
        with open(filename, "w") as f:
            json.dump(cube, f, indent=4)

    filename = os.path.join(path, "model.json")
    with open(filename, "w") as f:
        json.dump(metadata, f, indent=4)


def load_model(resource, translations=None):
    raise Exception("load_model() was replaced by Workspace.add_model(), "
                    "please refer to the documentation for more information")


class ModelProvider(object):
    """Abstract class. Currently empty and used only to find other model
    providers."""

    def __init__(self, metadata=None):
        """Base class for model providers. Initializes a model provider and
        sets `metadata` – a model metadata dictionary.

        Instance variable `store` might be populated after the
        initialization. If the model provider requires an open store, it
        should advertise it through `True` value returned by provider's
        `requires_store()` method.  Otherwise no store is opened for the model
        provider. `store_name` is also set.

        Subclasses should call this method at the beginning of the custom
        `__init__()`.

        If a model provider subclass has a metadata that should be pre-pended
        to the user-provided metadta, it should return it in
        `default_metadata()`.

        Subclasses should implement at least: :meth:`cubes.ModelProvider.cube`,
        :meth:`cubes.ModelProvider.dimension` and
        :meth:`cubes.ModelProvider.list_cubes`
        """

        self.store = None
        self.store_name = None

        # Get provider's defaults and pre-pend it to the user provided
        # metadtata.
        defaults = self.default_metadata()
        self.metadata = self._merge_metadata(defaults, metadata)

        # TODO: check for duplicates
        self.dimensions_metadata = {}
        for dim in self.metadata.get("dimensions", []):
            self.dimensions_metadata[dim["name"]] = dim

        self.cubes_metadata = {}
        for cube in self.metadata.get("cubes", []):
            self.cubes_metadata[cube["name"]] = cube

        # TODO: decide which one to use
        self.options = self.metadata.get("options", {})
        self.options.update(self.metadata.get("browser_options", {}))

    def _merge_metadata(self, metadata, other):
        """See `default_metadata()` for more information."""

        metadata = dict(metadata)
        other = dict(other)

        cubes = metadata.pop("cubes", []) + other.pop("cubes", [])
        if cubes:
            metadata["cubes"] = cubes

        dims = metadata.pop("dimensions", []) + other.pop("dimensions", [])
        if dims:
            metadata["dimensions"] = dims

        joins = metadata.pop("joins", []) + other.pop("joins",[])
        if joins:
            metadata["joins"] = joins

        mappings = metadata.pop("mappings", {})
        mappings.update(other.pop("mappings", {}))
        if mappings:
            metadata["mappings"] = mappings

        metadata.update(other)

        return metadata

    def default_metadata(self, metadata=None):
        """Returns metadata that are prepended to the provided model metadata.
        `metadata` is user-provided metadata and might be used to decide what
        kind of default metadata are returned.

        The metadata are merged as follows:

        * cube lists are concatenated (no duplicity checking)
        * dimension lists are concatenated (no duplicity checking)
        * joins are concatenated
        * default mappings are updated with the model's mappings

        Default implementation returns empty metadata.
        """

        return {}

    def requires_store(self):
        """Return `True` if the provider requires a store. Subclasses might
        override this method. Default implementation returns `False`"""
        return False

    def set_store(self, store, store_name):
        """Set's the provider's `store` and `store_name`. The store can be used
        to retrieve model's metadata. The store name is a handle that can be
        passed to the Cube objects for workspace to know where to find cube's
        data."""

        self.store = store
        self.store_name = store_name
        self.initialize_from_store()

    def initialize_from_store(self):
        """Sets provider's store and store name. This method is called after
        the provider's `store` and `store_name` were set. Override this method
        if you would like to perform post-initialization from the store."""
        pass

    def cube_options(self, cube_name):
        """Returns an options dictionary for cube `name`. The options
        dictoinary is merged model `options` metadata with cube's `options`
        metadata if exists. Cube overrides model's global (default)
        options."""

        options = dict(self.options)
        if cube_name in self.cubes_metadata:
            cube = self.cubes_metadata[cube_name]
            # TODO: decide which one to use
            options.update(cube.get("options", {}))
            options.update(cube.get("browser_options", {}))

        return options

    def dimension_metadata(self, name):
        """Returns a metadata dictionary for dimension `name`."""
        return self.dimensions_metadata[name]

    def cube_metadata(self, name):
        """Returns a cube metadata by combining model's global metadata and
        cube's metadata. Merged metadata dictionaries: `browser_options`,
        `mappings`, `joins`.
        """

        if name in self.cubes_metadata:
            metadata = dict(self.cubes_metadata[name])
        else:
            raise NoSuchCubeError("Unknown cube '%s'" % name, name)

        # merge datastore from model if datastore not present
        if not metadata.get("datastore"):
            metadata['datastore'] = self.metadata.get("datastore")

        # merge browser_options
        browser_options = self.metadata.get('browser_options', {})
        if metadata.get('browser_options'):
            browser_options.update(metadata.get('browser_options'))
        metadata['browser_options'] = browser_options

        # Merge model and cube mappings
        #
        model_mappings = self.metadata.get("mappings")
        cube_mappings = metadata.pop("mappings", {})

        if model_mappings:
            mappings = copy.deepcopy(model_mappings)
            mappings.update(cube_mappings)
        else:
            mappings = cube_mappings

        metadata["mappings"] = mappings

        # Merge model and cube joins
        #
        model_joins = self.metadata.get("joins", [])
        cube_joins = metadata.pop("joins", [])

        # model joins, if present, should be merged with cube's overrides.
        # joins are matched by the "name" key.
        if cube_joins and model_joins:
            model_join_map = {}
            for join in model_joins:
                try:
                    jname = join['name']
                except KeyError:
                    raise ModelError("Missing required 'name' key in "
                                     "model-level joins.")

                if jname in model_join_map:
                    raise ModelError("Duplicate model-level join 'name': %s" %
                                     jname)

                model_join_map[jname] = copy.deepcopy(join)

            # Merge cube's joins with model joins by their names.
            merged_joins = []

            for join in cube_joins:
                name = join.get('name')
                if name and model_join_map.has_key(name):
                    model_join = dict(model_join_map[name])
                else:
                    model_join = {}

                model_join.update(join)
                merged_joins.append(model_join)
        else:
            merged_joins = cube_joins

        # Validate joins:
        for join in merged_joins:
            if "master" not in join:
                raise ModelError("No master in join for cube '%s' "
                                 "(join name: %s)" % (name, join.get("name")))
            if "detail" not in join:
                raise ModelError("No detail in join for cube '%s' "
                                 "(join name: %s)" % (name, join.get("name")))

        metadata["joins"] = merged_joins

        return metadata

    def public_dimensions(self):
        """Returns a list of public dimension names. Default implementation
        returs all dimensions defined in the model metadata. If
        ``public_dimensions`` model property is set, then this list is used.

        Subclasses might override this method for alternative behavior. For
        example, if the backend uses dimension metadata from the model, but
        does not publish any dimension it can return an empty list."""
        # Get list of exported dimensions
        # By default all explicitly mentioned dimensions are exported.
        #
        try:
            return self.metadata["public_dimensions"]
        except KeyError:
            dimensions = self.metadata.get("dimensions", [])
            names = [dim["name"] for dim in dimensions]
            return names

    def list_cubes(self):
        """Get a list of metadata for cubes in the workspace. Result is a list
        of dictionaries with keys: `name`, `label`, `category`, `info`.

        The list is fetched from the model providers on the call of this
        method.

        Subclassees should implement this method.
        """
        raise NotImplementedError("Subclasses should implement list_cubes()")
        return []

    def cube(self, name, locale=None):
        """Returns a cube with `name` provided by the receiver. If receiver
        does not have the cube `NoSuchCube` exception is raised.

        Returned cube has no dimensions assigned. You should assign the
        dimensions according to the cubes `linked_dimensions` list of
        dimension names.

        Subclassees should implement this method.
        """
        raise NotImplementedError("Subclasses should implement cube() method")

    def dimension(self, name, templates=[]):
        """Returns a dimension with `name` provided by the receiver.
        `dimensions` is a dictionary of dimension objects where the receiver
        can look for templates. If the dimension requires a template and the
        template is missing, the subclasses should raise
        `TemplateRequired(template)` error with a template name as an
        argument.

        If the receiver does not provide the dimension `NoSuchDimension`
        exception is raised.

        Subclassees should implement this method.
        """
        raise NotImplementedError("Subclasses are required to implement this")


class StaticModelProvider(ModelProvider):

    dynamic_cubes = False
    dynamic_dimensions = False

    def __init__(self, *args, **kwargs):
        super(StaticModelProvider, self).__init__(*args, **kwargs)

    def list_cubes(self):
        """Returns a list of cubes from the metadata."""
        cubes = []

        for cube in self.metadata.get("cubes", []):
            info = {
                    "name": cube["name"],
                    "label": cube.get("label", cube["name"]),
                    "category": (cube.get("category") or cube.get("info", {}).get("category")),
                    "info": cube.get("info", {})
                }
            cubes.append(info)

        return cubes

    def cube(self, name):
        """
        Creates a cube `name` in context of `workspace` from provider's
        metadata. The created cube has no dimensions attached. You sohuld link
        the dimensions afterwards according to the `linked_dimensions`
        property of the cube.
        """

        metadata = self.cube_metadata(name)
        return create_cube(metadata)

    def dimension(self, name, locale=None, templates=None):
        """Create a dimension `name` from provider's metadata. `templates` is
        a dictionary with already instantiated dimensions to be used as
        templates"""

        try:
            metadata = dict(self.dimensions_metadata[name])
        except KeyError:
            raise NoSuchDimensionError(name)

        return create_dimension(metadata, templates, name)


# TODO: is this still necessary?
def merge_models(models):
    """Merge multiple models into one."""

    dimensions = {}
    all_cubes = {}
    name = None
    label = None
    description = None
    info = {}
    locale = None

    for model in models:
        if name is None and model.name:
            name = model.name
        if label is None and model.label:
            label = model.label
        if description is None and model.description:
            description = model.description
        if info is None and model.info:
            info = copy.deepcopy(model.info)
        if locale is None and model.locale:
            locale = model.locale

        # dimensions, fail on conflicting names
        for dim in model.dimensions:
            if dimensions.has_key(dim.name):
                raise ModelError("Found duplicate dimension named '%s', cannot merge models" % dim.name)
            dimensions[dim.name] = dim

        # cubes, fail on conflicting names
        for cube in model.cubes.values():
            if all_cubes.has_key(cube.name):
                raise ModelError("Found duplicate cube named '%s', cannot merge models" % cube.name)
            model.remove_cube(cube)
            if cube.info is None:
                cube.info = {}
            cube.info.update(model.info if model.info else {})
            all_cubes[cube.name] = cube

    return Model(name=name,
                 label=label,
                 description=description,
                 info=info,
                 dimensions=dimensions.values(),
                 cubes=all_cubes.values())


def create_model(source):
    raise NotImplementedError("create_model() is depreciated, use Workspace.add_model()")


def model_from_path(path):
    """Load logical model from a file or a directory specified by `path`.
    Returs instance of `Model`. """
    raise NotImplementedError("model_from_path is depreciated. use Workspace.add_model()")

# TODO: modernize
def simple_model(cube_name, dimensions, measures):
    """Create a simple model with only one cube with name `cube_name`and flat
    dimensions. `dimensions` is a list of dimension names as strings and
    `measures` is a list of measure names, also as strings. This is
    convenience method mostly for quick model creation for denormalized views
    or tables with data from a single CSV file.

    Example:

    .. code-block:: python

        model = simple_model("contracts",
                             dimensions=["year", "supplier", "subject"],
                             measures=["amount"])
        cube = model.cube("contracts")
        browser = workspace.create_browser(cube)
    """
    dim_instances = []
    for dim_name in dimensions:
        dim_instances.append(create_dimension(dim_name))

    cube = Cube(cube_name, dim_instances, measures)

    return Model(cubes=[cube])


ValidationError = namedtuple("ValidationError",
                            ["severity", "scope", "object", "property", "message"])


def validate_model(metadata):
    """Validate model metadata."""

    validator = ModelMetadataValidator(metadata)
    return validator.validate()

class ModelMetadataValidator(object):
    def __init__(self, metadata):
        self.metadata = metadata

        data = pkgutil.get_data("cubes", "schemas/model.json")
        self.model_schema = json.loads(data)

        data = pkgutil.get_data("cubes", "schemas/cube.json")
        self.cube_schema = json.loads(data)

        data = pkgutil.get_data("cubes", "schemas/dimension.json")
        self.dimension_schema = json.loads(data)

    def validate(self):
        errors = []

        errors += self.validate_model()

        if "cubes" in self.metadata:
            for cube in self.metadata["cubes"]:
                errors += self.validate_cube(cube)

        if "dimensions" in self.metadata:
            for dim in self.metadata["dimensions"]:
                errors += self.validate_dimension(dim)

        return errors

    def _collect_errors(self, scope, obj, validator, metadata):
        errors = []

        for error in validator.iter_errors(metadata):
            if error.path:
                path = [str(item) for item in error.path]
                ref = ".".join(path)
            else:
                ref = None

            verror = ValidationError("error", scope, obj, ref, error.message)
            errors.append(verror)

        return errors

    def validate_model(self):
        validator = jsonschema.Draft4Validator(self.model_schema)
        errors = self._collect_errors("model", None, validator, self.metadata)

        dims = self.metadata.get("dimensions")
        if dims and isinstance(dims, list):
            for dim in dims:
                if isinstance(dim, basestring):
                    err = ValidationError("default", "model", None,
                                          "dimensions",
                                          "Dimension '%s' is not described, "
                                          "creating flat single-attribute "
                                          "dimension" % dim)
                    errors.append(err)

        return errors

    def validate_cube(self, cube):
        validator = jsonschema.Draft4Validator(self.cube_schema)
        name = cube.get("name")

        return self._collect_errors("cube", name, validator, cube)

    def validate_dimension(self, dim):
        validator = jsonschema.Draft4Validator(self.dimension_schema)
        name = dim.get("name")

        errors = self._collect_errors("dimension", name, validator, dim)

        if "default_hierarchy_name" not in dim:
            error = ValidationError("default", "dimension", name, None,
                                    "No default hierarchy name specified, "
                                    "using first one")
            errors.append(error)

        if "levels" not in dim and "attributes" not in dim:
            error = ValidationError("default", "dimension", name, None,
                                    "Neither levels nor attributes specified, "
                                    "creating flat dimension without details")
            errors.append(error)

        elif "levels" in dim and "attributes" in dim:
            error = ValidationError("error", "dimension", name, None,
                                    "Both levels and attributes specified")
            errors.append(error)

        return errors
