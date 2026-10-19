// Grab frames from a movie as JPEGs. usage: grab_frames <movie> <out_prefix> <frame>... (frames at the movie's fps)
import Foundation
import AVFoundation
import CoreImage

let a = CommandLine.arguments
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let fps = asset.tracks(withMediaType: .video)[0].nominalFrameRate
let gen = AVAssetImageGenerator(asset: asset)
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero
gen.maximumSize = CGSize(width: 960, height: 540)
let ctx = CIContext()
for s in a[3...] {
  let f = Int(s)!
  let t = CMTime(value: CMTimeValue(f * 1000), timescale: CMTimeScale(fps * 1000))
  guard let cg = try? gen.copyCGImage(at: t, actualTime: nil) else { print("fail \(f)"); continue }
  let url = URL(fileURLWithPath: "\(a[2])\(String(format: "%04d", f)).jpg")
  try! ctx.writeJPEGRepresentation(of: CIImage(cgImage: cg), to: url, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
}
print("fps \(fps) duration \(CMTimeGetSeconds(asset.duration))")
