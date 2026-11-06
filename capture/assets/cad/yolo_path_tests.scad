/*
YOLO Print-Head Tracking Test Set
One design per export. Set `test` below, preview (F5), then export STL (F6).

Suggested Cura settings
- Layer height: 0.20 mm
- Wall line count: 1
- Top/bottom layers: 0
- Infill: 0%
- Supports: off
- Adhesion: skirt or brim only if required

Tests:
  "square_1", "square_2", "square_3"
  "zigzag_1", "zigzag_2"
  "curve_1", "curve_2"
  "normal_1", "normal_2", "normal_3", "normal_4", "normal_5"
*/

$fn = 96;
test = "square_1";
line_w = 0.48;
layer_h = 0.20;

// OpenSCAD 2D path helper: creates a rounded, extrudable path from points.
module path(points, width=line_w, h=layer_h) {
    linear_extrude(height=h)
        union() {
            for (i = [0 : len(points)-2])
                hull() {
                    translate(points[i]) circle(d=width);
                    translate(points[i+1]) circle(d=width);
                }
            for (p = points) translate(p) circle(d=width);
        }
}

module rounded_square(size=100, r=4) {
    linear_extrude(height=layer_h)
        difference() {
            offset(r=r) square([size-2*r, size-2*r], center=true);
            offset(delta=-line_w) offset(r=r) square([size-2*r, size-2*r], center=true);
        }
}

module circle_path(d=85) {
    linear_extrude(height=layer_h)
        difference() {
            circle(d=d);
            circle(d=d-2*line_w);
        }
}

module arc_path(r=52, a0=-145, a1=145, steps=80) {
    pts = [for (i=[0:steps]) let(a=a0+(a1-a0)*i/steps) [r*cos(a), r*sin(a)]];
    path(pts);
}

module spiral_path(r0=8, r1=48, turns=2.25, steps=160) {
    pts = [for (i=[0:steps]) let(t=i/steps, a=360*turns*t, r=r0+(r1-r0)*t) [r*cos(a), r*sin(a)]];
    path(pts);
}

module sample_square_1() { rounded_square(100, 4); }
module sample_square_2() { rounded_square(70, 3); }
module sample_square_3() { rounded_square(125, 5); }

module sample_zigzag_1() {
    path([[-80,-45],[-40,45],[0,-45],[40,45],[80,-45]]);
}

module sample_zigzag_2() {
    path([[-85,-50],[-55,50],[-25,-50],[5,50],[35,-50],[65,50],[85,-50]]);
}

module sample_curve_1() { circle_path(95); }
module sample_curve_2() { arc_path(); }

// Short multi-layer-style objects. Slice normally; they introduce progressively more occlusion/clutter.
module sample_normal_1() {
    // Hollow 30 mm cube: simple walls, low clutter.
    difference() {
        cube([30,30,12], center=true);
        translate([0,0,1]) cube([29.04,29.04,14], center=true);
    }
}

module sample_normal_2() {
    // Cylinder: continuously changing head direction.
    difference() {
        cylinder(h=18, d=34, center=false);
        translate([0,0,-1]) cylinder(h=20, d=33.04, center=false);
    }
}

module sample_normal_3() {
    // Two small separated towers: travel moves plus moderate occlusion.
    for (x=[-28,28])
        translate([x,0,0])
            difference() {
                cylinder(h=24, d=24);
                translate([0,0,-1]) cylinder(h=26, d=23.04);
            }
}

module sample_normal_4() {
    // Frame with cross ribs: more travel paths and interior features.
    difference() {
        cube([70,70,16], center=true);
        cube([62,62,18], center=true);
    }
    translate([0,0,8]) cube([64,line_w,layer_h], center=true);
    translate([0,0,8]) cube([line_w,64,layer_h], center=true);
}

module sample_normal_5() {
    // Three towers of varying height: strongest occlusion and changing background.
    translate([-28,-20,0]) cylinder(h=12,d=24);
    translate([28,-20,0]) cylinder(h=24,d=24);
    translate([0,28,0]) cylinder(h=36,d=24);
}

if (test == "square_1") sample_square_1();
else if (test == "square_2") sample_square_2();
else if (test == "square_3") sample_square_3();
else if (test == "zigzag_1") sample_zigzag_1();
else if (test == "zigzag_2") sample_zigzag_2();
else if (test == "curve_1") sample_curve_1();
else if (test == "curve_2") sample_curve_2();
else if (test == "normal_1") sample_normal_1();
else if (test == "normal_2") sample_normal_2();
else if (test == "normal_3") sample_normal_3();
else if (test == "normal_4") sample_normal_4();
else if (test == "normal_5") sample_normal_5();
else echo("Unknown test name.");
