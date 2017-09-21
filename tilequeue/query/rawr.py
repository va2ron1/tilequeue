from collections import namedtuple
from shapely.geometry import box
from tilequeue.query.common import layer_properties


class TilePyramid(namedtuple('TilePyramid', 'z x y max_z')):

    def tile(self):
        from raw_tiles.tile import Tile
        return Tile(self.z, self.x, self.y)

    def bounds(self):
        from ModestMaps.Core import Coordinate
        from tilequeue.tile import coord_to_mercator_bounds

        coord = Coordinate(zoom=self.z, column=self.x, row=self.y)
        bounds = coord_to_mercator_bounds(coord)

        return bounds

    def bbox(self):
        return box(*self.bounds())


class OsmRawrLookup(object):

    def relations_using_node(self, node_id):
        "Returns a list of relation IDs which contain the node with that ID."

        return []

    def relations_using_way(self, way_id):
        "Returns a list of relation IDs which contain the way with that ID."

        return []

    def relations_using_rel(self, rel_id):
        """
        Returns a list of relation IDs which contain the relation with that
        ID.
        """

        return []

    def ways_using_node(self, node_id):
        "Returns a list of way IDs which contain the node with that ID."

        return []

    def relation(self, rel_id):
        "Returns the Relation object with the given ID."

        return None

    def way(self, way_id):
        """
        Returns the feature (fid, shape, props) which was generated from the
        given way.
        """

        return None

    def node(self, node_id):
        """
        Returns the feature (fid, shape, props) which was generated from the
        given node.
        """

        return None

    def transit_relations(self, rel_id):
        "Return transit relations containing the relation with the given ID."

        return set()


class DataFetcher(object):

    def __init__(self, layers, tables, tile_pyramid):
        """
        Expect layers to be a dict of layer name to LayerInfo (see fixture.py).
        Tables should be a callable which returns a generator over the rows in
        the table when called with that table's name.
        """

        from raw_tiles.index.features import FeatureTileIndex
        from raw_tiles.index.index import index_table

        self.layers = layers
        self.tile_pyramid = tile_pyramid
        self.layer_indexes = {}

        tile = self.tile_pyramid.tile()
        max_zoom = self.tile_pyramid.max_z

        for layer_name, info in self.layers.items():
            meta = None

            def min_zoom(fid, shape, props):
                return info.min_zoom_fn(fid, shape, props, meta)

            layer_index = FeatureTileIndex(tile, max_zoom, min_zoom)

            for shape_type in ('point', 'line', 'polygon'):
                if not info.allows_shape_type(shape_type):
                    continue

                source = tables('planet_osm_' + shape_type)
                index_table(source, 'add_feature', layer_index)

            self.layer_indexes[layer_name] = layer_index

        self.osm = OsmRawrLookup()

    def _lookup(self, zoom, unpadded_bounds, layer_name):
        from tilequeue.tile import mercator_point_to_coord
        from raw_tiles.tile import Tile

        minx, miny, maxx, maxy = unpadded_bounds
        topleft = mercator_point_to_coord(zoom, minx, miny)
        bottomright = mercator_point_to_coord(zoom, maxx, maxy)
        index = self.layer_indexes[layer_name]

        # make sure that the bottom right coordinate is below and to the right
        # of the top left coordinate. it can happen that the coordinates are
        # mixed up due to small numerical precision artefacts being enlarged
        # by the conversion to integer and y-coordinate flip.
        assert topleft.zoom == bottomright.zoom
        bottomright.column = max(bottomright.column, topleft.column)
        bottomright.row = max(bottomright.row, topleft.row)

        features = []
        for x in range(int(topleft.column), int(bottomright.column) + 1):
            for y in range(int(topleft.row), int(bottomright.row) + 1):
                tile = Tile(zoom, x, y)
                features.extend(index(tile))
        return features

    def __call__(self, zoom, unpadded_bounds):
        read_rows = []
        bbox = box(*unpadded_bounds)

        # check that the call is fetching data which is actually within the
        # bounds of the tile pyramid. we don't have data outside of that, so
        # can't fulfil requests. if these assertions are tripping, it probably
        # indicates a programming error - has the wrong DataFetcher been
        # loaded?
        assert zoom <= self.tile_pyramid.max_z
        assert zoom >= self.tile_pyramid.z
        assert bbox.within(self.tile_pyramid.bbox())

        for layer_name, info in self.layers.items():

            for (fid, shape, props) in self._lookup(
                    zoom, unpadded_bounds, layer_name):
                # reject any feature which doesn't intersect the given bounds
                if bbox.disjoint(shape):
                    continue

                # place for assembing the read row as if from postgres
                read_row = {}

                layer_props = layer_properties(
                    fid, shape, props, layer_name, zoom, self.osm)

                read_row['__' + layer_name + '_properties__'] = layer_props
                read_row['__id__'] = fid
                read_row['__geometry__'] = bytes(shape.wkb)
                read_rows.append(read_row)

        return read_rows


# tables is a callable which should return a generator over the rows of the
# table when called with the table name.
def make_rawr_data_fetcher(layers, tables, tile_pyramid):
    return DataFetcher(layers, tables, tile_pyramid)
