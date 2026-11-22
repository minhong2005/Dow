import AppKit
let base=URL(fileURLWithPath:CommandLine.arguments[1])
let fs=FileManager.default
let dir=base.appendingPathComponent("icons")
try fs.createDirectory(at:dir,withIntermediateDirectories:true)
let symbols = [
    ("explorer", "sidebar.left"), ("home", "house"), ("globe", "globe"),
    ("search", "magnifyingglass"), ("plus", "plus"), ("split", "rectangle.split.2x1"),
    ("folder", "folder"), ("chevron", "chevron.right"), ("close", "xmark"),
    ("more", "ellipsis"), ("terminal", "terminal"), ("arrow", "arrow.up.right")
]
func bitmap(_ s:Int,_ draw:()->Void)->Data{
    let bitmap=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:s,pixelsHigh:s,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:bitmap)
    draw()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using:.png,properties:[:])!
}
// Original tool tiles for the source distribution.
for (key, letters) in [("claude", "Cl"), ("codex", "Cx"), ("antigravity", "Ag")] {
    let data = bitmap(96) {
        NSColor(white: 0.19, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 3, y: 3, width: 90, height: 90), xRadius: 20, yRadius: 20).fill()
        let attributes: [NSAttributedString.Key:Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 37, weight: .medium),
            .foregroundColor: NSColor(white: 0.91, alpha: 1)
        ]
        let title = NSAttributedString(string: letters, attributes: attributes)
        let size = title.size()
        title.draw(at: NSPoint(x: (96-size.width)/2, y: (96-size.height)/2))
    }
    try data.write(to: dir.appendingPathComponent(key + ".png"))
}
var css=""
for (key,name) in symbols {
    guard let img=NSImage(systemSymbolName:name,accessibilityDescription:nil)?.withSymbolConfiguration(.init(pointSize:42,weight:.medium)) else {fatalError(name)}
    let data=bitmap(64){
        let scale=min(58/img.size.width,58/img.size.height)
        let sz=NSSize(width:img.size.width*scale,height:img.size.height*scale)
        img.draw(in:NSRect(x:(64-sz.width)/2,y:(64-sz.height)/2,width:sz.width,height:sz.height))
    }
    try data.write(to:dir.appendingPathComponent(key+".png"))
    css += ".i-"+key+"{--icon:url('data:image/png;base64,"+data.base64EncodedString()+"')}\n"
}
try css.write(to:base.appendingPathComponent("symbols.css"),atomically:true,encoding:.utf8)
let iconset=URL(fileURLWithPath:CommandLine.arguments[2])
try fs.createDirectory(at:iconset,withIntermediateDirectories:true)
func drawDow(_ tone:CGFloat) {
    func polygon(_ points:[NSPoint],_ gray:CGFloat) {
        let path=NSBezierPath();path.move(to:points[0])
        for pt in points.dropFirst(){path.line(to:pt)}
        path.close();NSColor(white:gray,alpha:1).setFill();path.fill()
    }
    func pt(_ x:CGFloat,_ y:CGFloat)->NSPoint{NSPoint(x:x,y:y)}
    polygon([pt(98,86),pt(98,386),pt(206,336),pt(206,138)],tone*0.83)
    polygon([pt(98,386),pt(224,449),pt(423,344),pt(312,288)],tone)
    polygon([pt(312,288),pt(423,344),pt(423,183),pt(312,133)],tone*0.73)
    polygon([pt(98,86),pt(206,138),pt(312,189),pt(312,288),pt(423,344),pt(423,183),pt(228,83),pt(162,52)],tone*0.93)
}
let logo=bitmap(96){
    let tr=NSAffineTransform();tr.scale(by:96.0/455);tr.translateX(by:-36,yBy:-14);tr.concat()
    drawDow(0.94)
}
try logo.write(to:dir.appendingPathComponent("dow.png"))
for size in [16,32,128,256,512] {
    for factor in [1,2] {
        let px=size*factor
        let data=bitmap(px){
            let tr=NSAffineTransform();tr.scale(by:CGFloat(px)/512);tr.concat()
            let tile=NSBezierPath(roundedRect:NSRect(x:22,y:22,width:468,height:468),xRadius:102,yRadius:102)
            NSColor(white:0.11,alpha:1).setFill();tile.fill()
            tile.lineWidth=2;NSColor(white:0.25,alpha:1).setStroke();tile.stroke()
            let inner=NSAffineTransform();inner.translateX(by:28,yBy:24);inner.scale(by:0.89);inner.concat();drawDow(0.96)
        }
        try data.write(to:iconset.appendingPathComponent("icon_\(size)x\(size)"+(factor==2 ? "@2x":"")+".png"))
    }
}
