"""Export the pinned NCBI encoders to dynamic-int8 ONNX, one at a time."""
import json
import time
from pathlib import Path
from run import low_priority, MODEL_ROOT, REVISIONS, save, digest, peak_mib


def main():
    low_priority()
    print('Loading local export dependencies (one process)',flush=True)
    import numpy as np
    import torch
    torch.set_num_threads(1); torch.set_num_interop_threads(1)
    print('PyTorch ready',flush=True)
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType
    print('ONNX export dependencies ready',flush=True)
    from transformers import AutoModel, AutoTokenizer

    class CLS(torch.nn.Module):
        def __init__(self, model):
            super().__init__(); self.embeddings=model.embeddings; self.encoder=model.encoder

        def forward(self,input_ids,attention_mask,token_type_ids):
            hidden=self.embeddings(input_ids=input_ids,token_type_ids=token_type_ids)
            mask=(1.-attention_mask[:,None,None,:].to(hidden.dtype))*torch.finfo(hidden.dtype).min
            return self.encoder(hidden,attention_mask=mask,return_dict=False)[0][:,0,:]

    for kind in REVISIONS:
        directory=MODEL_ROOT/kind.lower(); output=directory/'encoder-int8.onnx'
        if output.exists() and (directory/'onnx-manifest.json').exists():
            continue
        tick=time.perf_counter()
        print('Export and quantize '+kind,flush=True)
        model=AutoModel.from_pretrained(directory,local_files_only=True,
            use_safetensors=True,attn_implementation='eager').eval()
        tokenizer=AutoTokenizer.from_pretrained(directory,local_files_only=True)
        example=tokenizer(['An adult with type 2 diabetes and renal impairment.',
                           'Breast cancer after chemotherapy.'],padding=True,return_tensors='pt')
        wrapper=CLS(model).eval()
        with torch.inference_mode():
            reference=model(**example).last_hidden_state[:,0,:].numpy()
            np.testing.assert_allclose(wrapper(**example).numpy(),reference,rtol=1e-4,atol=1e-4)
            torch.onnx.export(wrapper,tuple(example[k] for k in ['input_ids','attention_mask','token_type_ids']),
                str(directory/'encoder-float.onnx'),opset_version=17,dynamo=False,
                input_names=['input_ids','attention_mask','token_type_ids'],output_names=['embeddings'],
                dynamic_axes={**{name:{0:'batch',1:'sequence'} for name in example},
                              'embeddings':{0:'batch'}})
        quantize_dynamic(str(directory/'encoder-float.onnx'),str(output),weight_type=QuantType.QInt8,
                         per_channel=True,reduce_range=True)
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        session=ort.InferenceSession(str(output),sess_options=options,providers=['CPUExecutionProvider'])
        actual=session.run(None,{k:v.numpy() for k,v in example.items()})[0]
        cosine=np.sum(actual*reference,axis=1)/(np.linalg.norm(actual,axis=1)*np.linalg.norm(reference,axis=1))
        if not np.isfinite(actual).all() or cosine.min()<.98:
            raise RuntimeError('Quantization sanity check failed')
        save(directory/'onnx-manifest.json',dict(source=REVISIONS[kind],precision='dynamic int8 per-channel reduce-range',
            onnx_sha256=digest(output),float_onnx_sha256=digest(directory/'encoder-float.onnx'),
            sanity_cosines=cosine.tolist(),sanity_scope='Two sample texts only; not a retrieval quality evaluation',
            seconds=time.perf_counter()-tick,peak_process_mib=peak_mib(),torch=torch.__version__,ort=ort.__version__))
        print(json.dumps(json.loads((directory/'onnx-manifest.json').read_text())),flush=True)
        del model,wrapper,session


if __name__=='__main__': main()
