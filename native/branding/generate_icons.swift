// SPDX-License-Identifier: GPL-2.0-or-later
// Original Open Painter vector artwork, rasterized at every macOS icon size.
import AppKit
import Foundation
let destination=CommandLine.arguments[1]
func artwork(_ document: Bool) {
    let background=NSBezierPath(roundedRect:NSRect(x:70,y:70,width:884,height:884),xRadius:document ? 60:200,yRadius:document ? 60:200)
    NSColor(srgbRed:0.075,green:0.10,blue:0.15,alpha:1).setFill();background.fill()
    NSColor(srgbRed:0.09,green:0.72,blue:0.94,alpha:1).setStroke()
    let ring=NSBezierPath();ring.appendArc(withCenter:NSPoint(x:488,y:500),radius:253,startAngle:48,endAngle:370,clockwise:false)
    ring.lineWidth=80;ring.lineCapStyle = .round;ring.stroke()
    let handle=NSBezierPath();handle.move(to:NSPoint(x:490,y:466));handle.line(to:NSPoint(x:774,y:758))
    handle.lineWidth=94;handle.lineCapStyle = .round
    NSColor(srgbRed:0.91,green:0.97,blue:1,alpha:1).setStroke();handle.stroke()
    let brush=NSBezierPath();brush.move(to:NSPoint(x:486,y:484));brush.curve(to:NSPoint(x:324,y:324),controlPoint1:NSPoint(x:355,y:490),controlPoint2:NSPoint(x:430,y:370));brush.curve(to:NSPoint(x:522,y:432),controlPoint1:NSPoint(x:472,y:301),controlPoint2:NSPoint(x:559,y:352));brush.close()
    NSColor(srgbRed:0.09,green:0.72,blue:0.94,alpha:1).setFill();brush.fill()
    if document {
        let attrs:[NSAttributedString.Key:Any]=[.font:NSFont.boldSystemFont(ofSize:90),.foregroundColor:NSColor.white]
        ("BLEND" as NSString).draw(at:NSPoint(x:338,y:142),withAttributes:attrs)
    }
}
for document in [false,true] {
    let name=document ? "OpenPainterDocument":"OpenPainter"
    let directory=URL(fileURLWithPath:destination).appendingPathComponent(name+".iconset")
    try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
    for base in [16,32,128,256,512] {for scale in [1,2] {
        let size=base*scale
        let bitmap=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:size,pixelsHigh:size,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
        NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:bitmap)
        let transform=NSAffineTransform();transform.scale(by:CGFloat(size)/1024);transform.concat();artwork(document)
        NSGraphicsContext.restoreGraphicsState()
        let suffix=scale==2 ? "@2x":""
        try bitmap.representation(using:.png,properties:[:])!.write(to:directory.appendingPathComponent("icon_\(base)x\(base)\(suffix).png"))
    }}
}
