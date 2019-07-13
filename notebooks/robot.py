from dataclasses import dataclass


@dataclass
class Position2D:
    x: int
    y: int
        
@dataclass
class Robot:
    position: Position2D