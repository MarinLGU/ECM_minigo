from absl import flags
import functools
import logging
import os.path

#import tensorflow as tf

from tensorflow.contrib import summary
from tensorflow.contrib.tpu.python.tpu import tpu_estimator


import features as features_lib
import go
import symmetries



flags.DEFINE_integer('train_batch_size', 256,
                     'Batch size to use for train/eval evaluation. For GPU '
                     'this is batch size as expected. If \"use_tpu\" is set,'
                     'final batch size will be = train_batch_size * num_tpu_cores')

flags.DEFINE_integer('conv_width', 256 if go.N == 19 else 32,
                     'The width of each conv layer in the shared trunk.')

flags.DEFINE_integer('policy_conv_width', 2,
                     'The width of the policy conv layer.')

flags.DEFINE_integer('value_conv_width', 1,
                     'The width of the value conv layer.')

flags.DEFINE_integer('fc_width', 256 if go.N == 19 else 64,
                     'The width of the fully connected layer in value head.')

flags.DEFINE_integer('trunk_layers', go.N,
                     'The number of resnet layers in the shared trunk.')

flags.DEFINE_multi_integer('lr_boundaries', [400000, 600000],
                           'The number of steps at which the learning rate will decay')

flags.DEFINE_multi_float('lr_rates', [0.01, 0.001, 0.0001],
                         'The different learning rates')

flags.DEFINE_float('l2_strength', 1e-4,
                   'The L2 regularization parameter applied to weights.')

flags.DEFINE_float('value_cost_weight', 1.0,
                   'Scalar for value_cost, AGZ paper suggests 1/100 for '
                   'supervised learning')

flags.DEFINE_float('sgd_momentum', 0.9,
                   'Momentum parameter for learning rate.')

flags.DEFINE_string('work_dir', None,
                    'The Estimator working directory. Used to dump: '
                    'checkpoints, tensorboard logs, etc..')

flags.DEFINE_bool('use_tpu', False, 'Whether to use TPU for training.')

flags.DEFINE_bool('quantize', False,
                  'Whether create a quantized model. When loading a model for '
                  'inference, this must match how the model was trained.')

flags.DEFINE_integer('quant_delay', 700 * 1024,
                     'Number of training steps after which weights and '
                     'activations are quantized.')

flags.DEFINE_string(
    'tpu_name', None,
    'The Cloud TPU to use for training. This should be either the name used'
    'when creating the Cloud TPU, or a grpc://ip.address.of.tpu:8470 url.')

flags.register_multi_flags_validator(
    ['lr_boundaries', 'lr_rates'],
    lambda flags: len(flags['lr_boundaries']) == len(flags['lr_rates']) - 1,
    'Number of learning rates must be exactly one greater than the number of boundaries')

flags.DEFINE_integer(
    'iterations_per_loop', 128,
    help=('Number of steps to run on TPU before outfeeding metrics to the CPU.'
          ' If the number of iterations in the loop would exceed the number of'
          ' train steps, the loop will exit before reaching'
          ' --iterations_per_loop. The larger this value is, the higher the'
          ' utilization on the TPU.'))

flags.DEFINE_integer(
    'num_tpu_cores', default=8,
    help=('Number of TPU cores. For a single TPU device, this is 8 because each'
          ' TPU has 4 chips each with 2 cores.'))

flags.DEFINE_integer(
    'summary_steps', default=256,
    help='Number of steps between logging summary scalars.')

flags.DEFINE_integer(
    'keep_checkpoint_max', default=5, help='Number of checkpoints to keep.')

flags.DEFINE_bool(
    'use_random_symmetry', True,
    help='If true random symmetries be used when doing inference.')

flags.register_multi_flags_validator(
    ['use_tpu', 'iterations_per_loop', 'summary_steps'],
    lambda flags: (not flags['use_tpu'] or
                   flags['summary_steps'] % flags['iterations_per_loop'] == 0),
    'If use_tpu, summary_steps must be a multiple of iterations_per_loop')

FLAGS = flags.FLAGS




import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.autograd import Variable
import numpy as np





class DualNetwork():
    def __init__(self, save_file):
        self.save_file = save_file
        self.inference_input = None
        self.inference_output = None

        # config = tf.ConfigProto()
        # config.gpu_options.allow_growth = True  #pas besoin ? a vérifier

        # self.sess = tf.Session(graph=tf.Graph(), config=config)   # pas de sessions en pytorch

        self.initialize_graph()

    def initialize_graph(self):
        # with self.sess.graph.as_default(): on n'est pas dans une session

        #features, labels = get_inference_input()   #methode a definir ( pas de placeholders en pytorch_ definition dynamique)
        """
        Returns the feature, output tensors that get passed into model_fn
        return (tf.placeholder(tf.float32,
                               [None, go.N, go.N, features_lib.NEW_FEATURES_PLANES],
                               name='pos_tensor'),
                {'pi_tensor': tf.placeholder(tf.float32, [None, go.N * go.N + 1]),
                 'value_tensor': tf.placeholder(tf.float32, [None])})
        """

        params = FLAGS.flag_values_dict()
        logging.info('TPU inference is supported on C++ only. '
                     'DualNetwork will ignore use_tpu=True')
        params['use_tpu'] = False  #pas de TPU

        # il faut definir features et labels  pour les injecter dans model_fn ci dessous
        #rq : inference output est appele dans un sess run dans la methode run_runmany
        #mais en pytorch il est directement attribue, donc il faut peut etre bouger tout ca dans le else directement
        #et pas besoin de run la session puisque le tenseur est deja "calcule" en direct
        estimator_spec = model_fn(features, labels,
                                  tf.estimator.ModeKeys.PREDICT,
                                  params=params)   #model_fn a definir

        self.inference_input = features
        self.inference_output = estimator_spec.predictions

        if self.save_file is not None:
            self.initialize_weights(self.save_file)
        else:
            self.sess.run(tf.global_variables_initializer())  # a initialiser en pytorch


    def initialize_weights(self, save_file):
        # https://pytorch.org/docs/stable/torch.html?highlight=torch%20load#torch.load
        torch.load(save_file)

    def run(self, position):
        probs, values = self.run_many([position])
        return probs[0], values[0]

    def run_many(self, positions):
        processed = list(map(features_lib.extract_features, positions))
        if FLAGS.use_random_symmetry:
            syms_used, processed = symmetries.randomize_symmetries_feat(
                processed)


        #https://stackoverflow.com/questions/33610685/in-tensorflow-what-is-the-difference-between-session-run-and-tensor-eval
        #A REVOIR UNE FOIS MODEL_FN REDEFINI : Y aura til besoin de run inference output ou celui ci sera deja til calc
        outputs = self.sess.run(self.inference_output,
                                feed_dict={self.inference_input: processed})


        probabilities, value = outputs['policy_output'], outputs['value_output']
        if FLAGS.use_random_symmetry:
            probabilities = symmetries.invert_symmetries_pi(
                syms_used, probabilities)
        return probabilities, value




def get_inference_input():
    """Set up placeholders for input features/labels.

    Returns the feature, output tensors that get passed into model_fn."""
    return (tf.placeholder(tf.float32,
                           [None, go.N, go.N, features_lib.NEW_FEATURES_PLANES],
                           name='pos_tensor'),
            {'pi_tensor': tf.placeholder(tf.float32, [None, go.N * go.N + 1]),
             'value_tensor': tf.placeholder(tf.float32, [None])})



def model_fn(features, labels, mode, params):
    '''
    Args:
        features: tensor with shape
            [BATCH_SIZE, go.N, go.N, features_lib.NEW_FEATURES_PLANES]
        labels: dict from string to tensor with shape
            'pi_tensor': [BATCH_SIZE, go.N * go.N + 1]
            'value_tensor': [BATCH_SIZE]
        mode: a tf.estimator.ModeKeys (batchnorm params update for TRAIN only)
        params: A dictionary (Typically derived from the FLAGS object.)
    Returns: tf.estimator.EstimatorSpec with props
        mode: same as mode arg
        predictions: dict of tensors
            'policy': [BATCH_SIZE, go.N * go.N + 1]
            'value': [BATCH_SIZE]
        loss: a single value tensor
        train_op: train op
        eval_metric_ops
    return dict of tensors
        logits: [BATCH_SIZE, go.N * go.N + 1]
    '''

    #revenir ici une fois model_inference_fn redefini
    policy_output, value_output, logits = model_inference_fn(
        features, mode == tf.estimator.ModeKeys.TRAIN, params)   #changer l'argument ici

    # train ops
    policy_cost = tf.reduce_mean(
        tf.nn.softmax_cross_entropy_with_logits_v2(
            logits=logits, labels=tf.stop_gradient(labels['pi_tensor'])))

    value_cost = params['value_cost_weight'] * tf.reduce_mean(
        tf.square(value_output - labels['value_tensor']))

    reg_vars = [v for v in tf.trainable_variables()
                if 'bias' not in v.name and 'beta' not in v.name]
    l2_cost = params['l2_strength'] * \
        tf.add_n([tf.nn.l2_loss(v) for v in reg_vars])

    combined_cost = policy_cost + value_cost + l2_cost

    global_step = tf.train.get_or_create_global_step()
    learning_rate = tf.train.piecewise_constant(
        global_step, params['lr_boundaries'], params['lr_rates'])
    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)

    # Insert quantization ops if requested
    if params['quantize']:
        if mode == tf.estimator.ModeKeys.TRAIN:
            tf.contrib.quantize.create_training_graph(
                quant_delay=params['quant_delay'])
        else:
            tf.contrib.quantize.create_eval_graph()

    optimizer = tf.train.MomentumOptimizer(learning_rate, params['sgd_momentum'])
    with tf.control_dependencies(update_ops):
        train_op = optimizer.minimize(combined_cost, global_step=global_step)

    # Computations to be executed on CPU, outside of the main TPU queues.
    def eval_metrics_host_call_fn(policy_output, value_output, pi_tensor, policy_cost,
                                  value_cost, l2_cost, combined_cost, step,
                                  est_mode=tf.estimator.ModeKeys.TRAIN):
        policy_entropy = -tf.reduce_mean(tf.reduce_sum(
            policy_output * tf.log(policy_output), axis=1))
        # pi_tensor is one_hot when generated from sgfs (for supervised learning)
        # and soft-max when using self-play records. argmax normalizes the two.
        policy_target_top_1 = tf.argmax(pi_tensor, axis=1)

        policy_output_in_top1 = tf.to_float(
            tf.nn.in_top_k(policy_output, policy_target_top_1, k=1))
        policy_output_in_top3 = tf.to_float(
            tf.nn.in_top_k(policy_output, policy_target_top_1, k=3))

        policy_top_1_confidence = tf.reduce_max(policy_output, axis=1)
        policy_target_top_1_confidence = tf.boolean_mask(
            policy_output,
            tf.one_hot(policy_target_top_1, tf.shape(policy_output)[1]))

        with tf.variable_scope("metrics"):
            metric_ops = {
                'policy_cost': tf.metrics.mean(policy_cost),
                'value_cost': tf.metrics.mean(value_cost),
                'l2_cost': tf.metrics.mean(l2_cost),
                'policy_entropy': tf.metrics.mean(policy_entropy),
                'combined_cost': tf.metrics.mean(combined_cost),

                'policy_accuracy_top_1': tf.metrics.mean(policy_output_in_top1),
                'policy_accuracy_top_3': tf.metrics.mean(policy_output_in_top3),
                'policy_top_1_confidence': tf.metrics.mean(policy_top_1_confidence),
                'policy_target_top_1_confidence': tf.metrics.mean(
                    policy_target_top_1_confidence),
                'value_confidence': tf.metrics.mean(tf.abs(value_output)),
            }

        if est_mode == tf.estimator.ModeKeys.EVAL:
            return metric_ops

        # NOTE: global_step is rounded to a multiple of FLAGS.summary_steps.
        eval_step = tf.reduce_min(step)

        # Create summary ops so that they show up in SUMMARIES collection
        # That way, they get logged automatically during training
        summary_writer = summary.create_file_writer(FLAGS.work_dir)
        with summary_writer.as_default(), \
                summary.record_summaries_every_n_global_steps(
                    params['summary_steps'], eval_step):
            for metric_name, metric_op in metric_ops.items():
                summary.scalar(metric_name, metric_op[1], step=eval_step)

        # Reset metrics occasionally so that they are mean of recent batches.
        reset_op = tf.variables_initializer(tf.local_variables("metrics"))
        cond_reset_op = tf.cond(
            tf.equal(eval_step % params['summary_steps'], tf.to_int64(1)),
            lambda: reset_op,
            lambda: tf.no_op())

        return summary.all_summary_ops() + [cond_reset_op]

    metric_args = [
        policy_output,
        value_output,
        labels['pi_tensor'],
        tf.reshape(policy_cost, [1]),
        tf.reshape(value_cost, [1]),
        tf.reshape(l2_cost, [1]),
        tf.reshape(combined_cost, [1]),
        tf.reshape(global_step, [1]),
    ]

    predictions = {
        'policy_output': policy_output,
        'value_output': value_output,
    }

    eval_metrics_only_fn = functools.partial(
        eval_metrics_host_call_fn, est_mode=tf.estimator.ModeKeys.EVAL)
    host_call_fn = functools.partial(
        eval_metrics_host_call_fn, est_mode=tf.estimator.ModeKeys.TRAIN)

    tpu_estimator_spec = tpu_estimator.TPUEstimatorSpec(
        mode=mode,
        predictions=predictions,
        loss=combined_cost,
        train_op=train_op,
        eval_metrics=(eval_metrics_only_fn, metric_args),
        host_call=(host_call_fn, metric_args)
    )

    return tpu_estimator_spec.as_estimator_spec()



#revoir l.237 ensuite (la ou est appelee la fn)


#https://github.com/junxiaosong/AlphaZero_Gomoku/blob/master/policy_value_net_pytorch.py
#https://github.com/kdubovikov/tf-vs-pytorch/blob/master/pytorch_vs_tf_simple_model.ipynb

#regarder ca pour voir la definition du reseau : utiliser les classes ???

class My_batchn(nn.Module):
    def __init__(self):
        super(My_batchn, self).__init__()

        self.batchn = torch.nn.BatchNorm2d(num_features, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True)

    def forward(self, x):
        x = F.relu(self.batchn(x))
        return x


class My_conv2d(nn.Module):
    def __init__(self, conv_width, kernel_size):
        super(My_conv2d, self).__init__()
        self.conv = torch.nn.Conv2d(1, conv_width, kernel_size, stride=1, padding=2)

    def forward(self, x):
        x = F.relu(self.conv(x))
        return x


class My_res_layer(nn.Module):
    def __init__(self, num_features):
        super(My_res_layer, self).__init__()
        self.layer1 = nn.Sequential(
            nn.BatchNorm2d(num_features, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True),
            nn.Conv2d(1, 32, kernel_size=5, stride=1, padding=2),
            nn.ReLU())
        self.layer2 = nn.Sequential(
            nn.BatchNorm2d(num_features, eps=1e-05, momentum=0.1, affine=True, track_running_stats=True),
            nn.Conv2d(3, 18, kernel_size=3, stride=1, padding=1),
            nn.ReLU())

    def forward(self, x):
        out = self.layer1(x)
        initial_output = out
        out = self.layer2(out)

        return initial_output,out


def model_inference_fn(features, training, params):
    my_res_layer = functools.partial(My_res_layer())
    my_batchn= functools.partial(My_batchn())
    initial_output  = my_res_layer(features)[0]

    # the shared stack
    shared_output = initial_output
    for _ in range(params['trunk_layers']):
        shared_output = my_res_layer(shared_output)[1]


    # policy head
    policy_conv_net = functools.partial(My_conv2d(conv_width=params['policy_conv_width'], kernel_size=1))
    policy_conv = policy_conv_net(shared_output)
    policy_conv = F.relu(my_batchn(policy_conv))



    logits = tf.layers.dense(
        tf.reshape(policy_conv, [-1, params['policy_conv_width'] * go.N * go.N]),
        go.N * go.N + 1)

    #FAIRE UNE CLASSE LOGITS QUI CONTIENT UNE COUCHE DENSE
    logits_dense_net = logit()

    policy_output = F.softmax(logits_dense_net(policy_conv))

    # value head
    value_conv_net = functools.partial(My_conv2d(conv_width=params['value_conv_width'], kernel_size=1))
    value_conv = value_conv_net(shared_output)
    value_conv = F.relu(my_batchn(value_conv))


    #Idem trouver couche dense en pytorch
    value_fc_hidden = tf.nn.relu(tf.layers.dense(
        tf.reshape(value_conv, [-1, params['value_conv_width'] * go.N * go.N]),
        params['fc_width']))
    value_output = tf.nn.tanh(
        tf.reshape(tf.layers.dense(value_fc_hidden, 1), [-1]),
        name='value_output')

    return policy_output, value_output, logits



def const_model_inference_fn(features):
    """Builds the model graph with weights marked as constant.

    This improves TPU inference performance because it prevents the weights
    being transferred to the TPU every call to Session.run().

    Returns:
        (policy_output, value_output, logits) tuple of tensors.
    """
    def custom_getter(getter, name, *args, **kwargs):
        with tf.control_dependencies(None):
            return tf.guarantee_const(
                getter(name, *args, **kwargs), name=name + "/GuaranteeConst")
    with tf.variable_scope("", custom_getter=custom_getter):
        return model_inference_fn(features, False, FLAGS.flag_values_dict())


def get_estimator():
    return _get_nontpu_estimator()


def _get_nontpu_estimator():
    run_config = tf.estimator.RunConfig(
        save_summary_steps=FLAGS.summary_steps,
        keep_checkpoint_max=FLAGS.keep_checkpoint_max)
    return tf.estimator.Estimator(
        model_fn,
        model_dir=FLAGS.work_dir,
        config=run_config,
        params=FLAGS.flag_values_dict())





def bootstrap():
    """Initialize a tf.Estimator run with random initial weights."""
    # a bit hacky - forge an initial checkpoint with the name that subsequent
    # Estimator runs will expect to find.
    #
    # Estimator will do this automatically when you call train(), but calling
    # train() requires data, and I didn't feel like creating training data in
    # order to run the full train pipeline for 1 step.
    initial_checkpoint_name = 'model.ckpt-1'
    save_file = os.path.join(FLAGS.work_dir, initial_checkpoint_name)
    sess = tf.Session(graph=tf.Graph())
    with sess.graph.as_default():
        features, labels = get_inference_input()
        model_fn(features, labels, tf.estimator.ModeKeys.PREDICT,
                 params=FLAGS.flag_values_dict())
        sess.run(tf.global_variables_initializer())
        tf.train.Saver().save(sess, save_file)


def export_model(model_path):
    """Take the latest checkpoint and export it to model_path.

    Assumes that all relevant model files are prefixed by the same name.
    (For example, foo.index, foo.meta and foo.data-00000-of-00001).

    Args:
        model_path: The path (can be a gs:// path) to export model
    """
    estimator = tf.estimator.Estimator(model_fn, model_dir=FLAGS.work_dir,
                                       params=FLAGS.flag_values_dict())
    latest_checkpoint = estimator.latest_checkpoint()
    all_checkpoint_files = tf.gfile.Glob(latest_checkpoint + '*')
    for filename in all_checkpoint_files:
        suffix = filename.partition(latest_checkpoint)[2]
        destination_path = model_path + suffix
        print("Copying {} to {}".format(filename, destination_path))
        tf.gfile.Copy(filename, destination_path)


def freeze_graph(model_path):
    n = DualNetwork(model_path)
    out_graph = tf.graph_util.convert_variables_to_constants(
        n.sess, n.sess.graph.as_graph_def(), ["policy_output", "value_output"])
    with tf.gfile.GFile(model_path + '.pb', 'wb') as f:
        f.write(out_graph.SerializeToString())