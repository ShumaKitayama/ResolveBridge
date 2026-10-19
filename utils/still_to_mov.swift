// Still image -> 1920x1080 ProRes clip (scaled to fill the width, centre-cropped), fixed length.
// usage: still_to_mov <in image> <out.mov> <frames> [fps=24]
import Foundation
import AVFoundation
import CoreImage

let a = CommandLine.arguments
guard a.count >= 4 else { print("usage: still_to_mov <in> <out.mov> <frames> [fps]"); exit(1) }
let inURL = URL(fileURLWithPath: a[1]), outURL = URL(fileURLWithPath: a[2])
let frames = Int(a[3])!, fps = Int32(a.count > 4 ? a[4] : "24")!
let W = 1920, H = 1080

var ci = CIImage(contentsOf: inURL, options: [.applyOrientationProperty: true])!
let s = CGFloat(W) / ci.extent.width
ci = ci.transformed(by: CGAffineTransform(scaleX: s, y: s))
ci = ci.transformed(by: CGAffineTransform(translationX: -ci.extent.minX, y: -(ci.extent.height - CGFloat(H)) / 2 - ci.extent.minY))
ci = ci.cropped(to: CGRect(x: 0, y: 0, width: W, height: H))

try? FileManager.default.removeItem(at: outURL)
let w = try AVAssetWriter(outputURL: outURL, fileType: .mov)
let inp = AVAssetWriterInput(mediaType: .video, outputSettings: [
  AVVideoCodecKey: AVVideoCodecType.proRes422HQ, AVVideoWidthKey: W, AVVideoHeightKey: H])
let ad = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: inp, sourcePixelBufferAttributes: [
  kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: W, kCVPixelBufferHeightKey as String: H])
w.add(inp); w.startWriting(); w.startSession(atSourceTime: .zero)
let ctx = CIContext()
var pb: CVPixelBuffer?
CVPixelBufferPoolCreatePixelBuffer(nil, ad.pixelBufferPool!, &pb)
ctx.render(ci, to: pb!, bounds: CGRect(x: 0, y: 0, width: W, height: H), colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
for f in 0..<frames {
  while !inp.isReadyForMoreMediaData { usleep(1000) }
  ad.append(pb!, withPresentationTime: CMTime(value: CMTimeValue(f), timescale: fps))
}
inp.markAsFinished()
let sem = DispatchSemaphore(value: 0)
w.finishWriting { sem.signal() }
sem.wait()
print(w.status == .completed ? "ok \(outURL.path)" : "fail \(String(describing: w.error))")
