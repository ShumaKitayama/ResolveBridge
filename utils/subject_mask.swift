// Subject (foreground instance) mattes via macOS Vision.
// usage: subject_mask <input image|video> <out_dir> [maxInstances=2]
// Writes grayscale PNGs: <name>_inst<k>.png (stills) or <name>_inst<k>/f_%05d.png (video),
// instances ordered by area (inst1 = largest), plus combined "all".
import Foundation
import Vision
import AVFoundation
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: subject_mask <in> <out_dir> [maxInst]"); exit(1) }
let inURL = URL(fileURLWithPath: args[1])
let outDir = URL(fileURLWithPath: args[2])
let maxInst = args.count > 3 ? Int(args[3])! : 2
try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
let ctx = CIContext()
let base = inURL.deletingPathExtension().lastPathComponent

func writePNG(_ pb: CVPixelBuffer, _ url: URL) {
    var ci = CIImage(cvPixelBuffer: pb)
    ci = ci.applyingFilter("CIColorClamp")
    let cs = CGColorSpaceCreateDeviceGray()
    guard let cg = ctx.createCGImage(ci, from: ci.extent, format: .L8, colorSpace: cs) else { return }
    let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, cg, nil)
    CGImageDestinationFinalize(dest)
}

func area(_ obs: VNInstanceMaskObservation, _ i: Int) -> Int {
    guard let pb = try? obs.generateMask(forInstances: IndexSet(integer: i)) else { return 0 }
    CVPixelBufferLockBaseAddress(pb, .readOnly); defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
    let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb), rb = CVPixelBufferGetBytesPerRow(pb)
    let p = CVPixelBufferGetBaseAddress(pb)!
    var n = 0
    for y in 0..<h { let row = p.advanced(by: y * rb).assumingMemoryBound(to: Float32.self)
        for x in 0..<w where row[x] > 0.5 { n += 1 } }
    return n
}

// Per-instance centroid x is also used to keep identities stable across video frames.
func process(handler: VNImageRequestHandler, frame: Int?) {
    let req = VNGenerateForegroundInstanceMaskRequest()
    do { try handler.perform([req]) } catch { print("err \(error)"); return }
    guard let obs = req.results?.first else { print("no subject frame \(frame ?? -1)"); return }
    let inst = Array(obs.allInstances).sorted { area(obs, $0) > area(obs, $1) }
    func dest(_ tag: String) -> URL {
        if let f = frame {
            let d = outDir.appendingPathComponent("\(base)_\(tag)")
            try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
            return d.appendingPathComponent(String(format: "f_%05d.png", f))
        }
        return outDir.appendingPathComponent("\(base)_\(tag).png")
    }
    if let all = try? obs.generateScaledMaskForImage(forInstances: obs.allInstances, from: handler) {
        writePNG(all, dest("all"))
    }
    for k in 0..<maxInst {
        if k < inst.count, let m = try? obs.generateScaledMaskForImage(forInstances: IndexSet(integer: inst[k]), from: handler) {
            writePNG(m, dest("inst\(k + 1)"))
        }
    }
    if frame == nil { print("instances: \(inst.count)") }
}

let ext = inURL.pathExtension.lowercased()
if ["mov", "mp4", "m4v"].contains(ext) {
    let asset = AVURLAsset(url: inURL)
    let track = asset.tracks(withMediaType: .video)[0]
    let reader = try AVAssetReader(asset: asset)
    let out = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
    reader.add(out); reader.startReading()
    var f = 0
    while let sb = out.copyNextSampleBuffer() {
        if let pb = CMSampleBufferGetImageBuffer(sb) {
            // preferredTransform is ignored: these clips are landscape already
            process(handler: VNImageRequestHandler(cvPixelBuffer: pb, options: [:]), frame: f)
        }
        f += 1
        if f % 50 == 0 { print("frame \(f)") }
    }
    print("frames: \(f)")
} else {
    // Respect EXIF orientation of HEIC/JPEG stills by rendering through CIImage.
    let ci = CIImage(contentsOf: inURL, options: [.applyOrientationProperty: true])!
    process(handler: VNImageRequestHandler(ciImage: ci, options: [:]), frame: nil)
}
