"""Geometry-grounded navigation over FF7's live walkmesh adjacency graph."""
import heapq
import math


class NoPath(RuntimeError):
    pass


def center(triangle):
    return [sum(v[k] for v in triangle['vertices'])/3 for k in range(3)]


def contains(triangle, point):
    a, b, c = triangle['vertices']
    def cross(p, q):
        return (q[0]-p[0])*(point[1]-p[1])-(q[1]-p[1])*(point[0]-p[0])
    signs = [cross(a, b), cross(b, c), cross(c, a)]
    area = (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    return abs(area) > 0.001 and (min(signs) >= -0.01 or max(signs) <= 0.01)


def locate(mesh, point, hint=None):
    if hint is not None and 0 <= hint < len(mesh) and contains(mesh[hint], point):
        return hint
    candidates = [t for t in mesh if not t['locked'] and contains(t, point)]
    if not candidates:
        raise NoPath('Target is outside the walkmesh.')
    if len(point) < 3 and len(candidates) > 1:
        # Shared edges on one floor are OK; overlapping floors are ambiguous.
        heights = [center(t)[2] for t in candidates]
        if max(heights)-min(heights) > 32:
            raise NoPath('Target overlaps multiple floors; provide a triangle or Z coordinate.')
    return min(candidates, key=lambda t: abs(center(t)[2]-(point[2] if len(point)>2 else 0)))['id']


def route(mesh, start, goal):
    if not 0 <= start < len(mesh) or not 0 <= goal < len(mesh) or mesh[goal]['locked']:
        raise NoPath('Invalid or locked target triangle.')
    centers = [center(t) for t in mesh]
    queue, cost, parent = [(0, start)], {start: 0}, {}
    while queue:
        _, node = heapq.heappop(queue)
        if node == goal:
            path = [node]
            while node != start:
                node = parent[node]; path.append(node)
            return path[::-1]
        for neighbor in mesh[node]['neighbors']:
            if neighbor < 0 or mesh[neighbor]['locked']:
                continue
            new = cost[node]+math.dist(centers[node], centers[neighbor])
            if new < cost.get(neighbor, math.inf):
                cost[neighbor], parent[neighbor] = new, node
                heapq.heappush(queue, (new+math.dist(centers[neighbor], centers[goal]), neighbor))
    raise NoPath('No unlocked walkmesh path reaches the target.')


def portal(mesh, source, destination):
    triangle = mesh[source]
    edge = triangle['neighbors'].index(destination)
    a, b = triangle['vertices'][edge], triangle['vertices'][(edge+1)%3]
    return [(a[k]+b[k])/2 for k in range(3)]


def segment_distance(point, start, end):
    delta = [end[k]-start[k] for k in range(2)]
    length2 = sum(x*x for x in delta)
    fraction = max(0, min(1, sum((point[k]-start[k])*delta[k] for k in range(2))/length2)) if length2 else 0
    return math.hypot(*(point[k]-start[k]-fraction*delta[k] for k in range(2)))


def line_distance(point, a, b):
    length = math.dist(a[:2], b[:2])
    return abs((b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0]))/length if length else math.inf


def approaches_wall(start, end, a, b, radius):
    length2 = sum((b[k]-a[k])**2 for k in range(2))
    if not length2:
        return False
    projection = sum((end[k]-a[k])*(b[k]-a[k]) for k in range(2))/length2
    # A neighboring wall's infinite extension is not an obstacle. Its shared
    # corner is covered by the adjoining segment when the route turns there.
    return 0 <= projection <= 1 and line_distance(end,a,b) < radius and line_distance(end,a,b) < line_distance(start,a,b)-0.1


def obstacles(state):
    player = state['player']
    return [(a['position'], a['collision_radius']+player.get('collision_radius', 0)+2)
            for a in state.get('actors', []) if a['solid'] and a['visible']
            and abs(a['position'][2]-player['position'][2]) < 64]


def boundaries(state):
    """Nearby wall segments, respecting height on overlapping floors."""
    mesh, position = state['navigation']['mesh'], state['player']['position']
    result = []
    for triangle in mesh:
        for edge, neighbor in enumerate(triangle['neighbors']):
            if neighbor >= 0 and not mesh[neighbor]['locked']:
                continue
            a, b = triangle['vertices'][edge], triangle['vertices'][(edge+1)%3]
            length2 = sum((b[k]-a[k])**2 for k in range(2))
            fraction = max(0, min(1, sum((position[k]-a[k])*(b[k]-a[k]) for k in range(2))/length2)) if length2 else 0
            height = a[2]+fraction*(b[2]-a[2])
            if abs(height-position[2]) < 32 and segment_distance(position,a,b) < 80:
                result.append((a,b))
    return result


def waypoint(state, target):
    mesh = state['navigation']['mesh']
    player = state['player']
    goal = locate(mesh, target['position'], target.get('triangle'))
    path = route(mesh, player['triangle'], goal)
    if len(path) == 1:
        return target['position'], path
    point = portal(mesh, path[0], path[1])
    blockers = obstacles(state)
    if blockers:
        edge = mesh[path[0]]['neighbors'].index(path[1])
        a, b = mesh[path[0]]['vertices'][edge], mesh[path[0]]['vertices'][(edge+1)%3]
        candidates = [[a[k]+(b[k]-a[k])*fraction/10 for k in range(3)] for fraction in range(1, 10)]
        # An NPC can occupy the midpoint of an otherwise open doorway. Select
        # a clear part of that same edge; never route through an off-mesh point.
        def score(candidate):
            clearance = min(segment_distance(p, player['position'], candidate)-radius for p, radius in blockers)
            return (max(0, -clearance), math.dist(candidate[:2], point[:2]))
        point = min(candidates, key=score)
    # Push across the edge when close, so arrival tolerance cannot leave us
    # stuck on the original triangle. The segment stays in the two triangles.
    if math.dist(player['position'][:2], point[:2]) < 24:
        next_center = center(mesh[path[1]])
        length = math.dist(point[:2], next_center[:2])
        if length:
            pushed = [point[k]+(next_center[k]-point[k])*min(1, 32/length) for k in range(3)]
            if all(segment_distance(p, player['position'], pushed) >= radius for p, radius in blockers):
                point = pushed
    return point, path


def steering(state, target):
    ranked, path = steering_options(state, target)
    if ranked is None:
        return None, path
    return ranked[0][1], path


def steering_options(state, target):
    """Advancing directions, best first. The executor falls back to the
    second entry when the best one stalls against unmodeled geometry."""
    point, path = waypoint(state, target)
    position = state['player']['position']
    delta = [point[i]-position[i] for i in range(2)]
    distance = math.hypot(*delta)
    if distance < 0.1:
        return None, path
    directions = state['navigation']['direction_vectors']
    blockers = obstacles(state)
    walls = boundaries(state)
    radius = state['player'].get('collision_radius', 0)
    scored, against_wall = [], []
    for name, vector in directions.items():
        # Use a short probe: a long prediction can reject both cardinal moves
        # at a narrow corner even though alternating short moves can pass it.
        end = [position[k]+vector[k]*8 for k in range(2)]
        if any(segment_distance(p, position, end) < radius and
               math.dist(end, p[:2]) <= math.dist(position[:2], p[:2]) for p, radius in blockers):
            continue
        # A portal can be reachable for a point but put Cloud's collision body
        # against a corridor wall. Prefer the advancing tangent in that case.
        score = sum(delta[k]*vector[k] for k in range(2))/distance
        if any(approaches_wall(position,end,a,b,radius) for a,b in walls):
            against_wall.append((score, name))
            continue
        scored.append((score, name))
    if not scored or max(scored)[0] <= 0:
        # Already inside the body radius of walls on several sides (a pinch or
        # corner): every probe "approaches" some wall. The game slides the
        # body along walls, so take the best advancing direction anyway.
        # Characters are still never walked into.
        scored += against_wall
    if not scored:
        raise NoPath('Nearby characters block all movement directions.')
    scored.sort(reverse=True)
    if scored[0][0] <= 0:
        raise NoPath('No direction advances the route.')
    return [(score, name) for score, name in scored if score > 0], path
