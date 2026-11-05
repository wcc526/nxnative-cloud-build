"""Declare documented SDK-27 setters while retaining the runtime OS gate.

Apple's Objective-C MetalFX documentation declares these offsets as NSUInteger:
https://developer.apple.com/documentation/metalfx/mtlfxtemporalscalerbase/colorcontentoffsetx?language=objc
The SDK-26 compiler otherwise rejects the existing @available(iOS 27.0) body:
runtime availability checks do not add declarations to an older SDK.
"""
import hashlib

ORIGINAL_SHA='ccf617b51752c1e569ab1af3a63f99766f782cc3bdeaba35b3817a9e3d8f837b'
ANCHOR='typedef struct { uint64_t net; id<MTL4FXTemporalScaler> sc; NSUInteger key[9]; int fresh; } dlss_scaler;'
DECLARATION='''// Public SDK-27 setter signatures for builds made with SDK 26. Calls remain
// inside the existing macOS/iOS 27 availability gate; older systems are unchanged.
@protocol DMSDK27TemporalScalerOffsets <MTL4FXTemporalScaler>
@property(nonatomic) NSUInteger colorContentOffsetX;
@property(nonatomic) NSUInteger colorContentOffsetY;
@property(nonatomic) NSUInteger depthContentOffsetX;
@property(nonatomic) NSUInteger depthContentOffsetY;
@property(nonatomic) NSUInteger motionContentOffsetX;
@property(nonatomic) NSUInteger motionContentOffsetY;
@property(nonatomic) NSUInteger outputOffsetX;
@property(nonatomic) NSUInteger outputOffsetY;
@end
'''
OLD='''            sc.colorContentOffsetX = (NSUInteger)a.base[DLSS_COLOR][0]; sc.colorContentOffsetY = (NSUInteger)a.base[DLSS_COLOR][1];
            sc.depthContentOffsetX = (NSUInteger)a.base[DLSS_DEPTH][0]; sc.depthContentOffsetY = (NSUInteger)a.base[DLSS_DEPTH][1];
            sc.motionContentOffsetX = (NSUInteger)a.base[DLSS_MOTION][0]; sc.motionContentOffsetY = (NSUInteger)a.base[DLSS_MOTION][1];
            sc.outputOffsetX = (NSUInteger)a.base[DLSS_OUTPUT][0]; sc.outputOffsetY = (NSUInteger)a.base[DLSS_OUTPUT][1];'''


def apply(text):
    assert hashlib.sha256(text.encode()).hexdigest()==ORIGINAL_SHA
    assert text.count(ANCHOR)==1 and text.count(OLD)==1
    replacement='            id<DMSDK27TemporalScalerOffsets> offsets = (id<DMSDK27TemporalScalerOffsets>)sc;\n'+OLD.replace('sc.','offsets.')
    result=text.replace(ANCHOR,DECLARATION+ANCHOR).replace(OLD,replacement)
    assert result.count('if (@available(macOS 27.0, iOS 27.0, *))')==text.count('if (@available(macOS 27.0, iOS 27.0, *))')
    return result
