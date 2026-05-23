"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");
var _typeof = require("@babel/runtime/helpers/typeof");
Object.defineProperty(exports, "__esModule", {
  value: true
});
exports["default"] = void 0;
var _slicedToArray2 = _interopRequireDefault(require("@babel/runtime/helpers/slicedToArray"));
var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));
var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));
var _possibleConstructorReturn2 = _interopRequireDefault(require("@babel/runtime/helpers/possibleConstructorReturn"));
var _getPrototypeOf2 = _interopRequireDefault(require("@babel/runtime/helpers/getPrototypeOf"));
var _inherits2 = _interopRequireDefault(require("@babel/runtime/helpers/inherits"));
var _react = _interopRequireWildcard(require("react"));
var _antd = require("antd");
var _icons = require("@ant-design/icons");
function _getRequireWildcardCache(e) { if ("function" != typeof WeakMap) return null; var r = new WeakMap(), t = new WeakMap(); return (_getRequireWildcardCache = function _getRequireWildcardCache(e) { return e ? t : r; })(e); }
function _interopRequireWildcard(e, r) { if (!r && e && e.__esModule) return e; if (null === e || "object" != _typeof(e) && "function" != typeof e) return { "default": e }; var t = _getRequireWildcardCache(r); if (t && t.has(e)) return t.get(e); var n = { __proto__: null }, a = Object.defineProperty && Object.getOwnPropertyDescriptor; for (var u in e) if ("default" !== u && {}.hasOwnProperty.call(e, u)) { var i = a ? Object.getOwnPropertyDescriptor(e, u) : null; i && (i.get || i.set) ? Object.defineProperty(n, u, i) : n[u] = e[u]; } return n["default"] = e, t && t.set(e, n), n; }
function _callSuper(t, o, e) { return o = (0, _getPrototypeOf2["default"])(o), (0, _possibleConstructorReturn2["default"])(t, _isNativeReflectConstruct() ? Reflect.construct(o, e || [], (0, _getPrototypeOf2["default"])(t).constructor) : o.apply(t, e)); }
function _isNativeReflectConstruct() { try { var t = !Boolean.prototype.valueOf.call(Reflect.construct(Boolean, [], function () {})); } catch (t) {} return (_isNativeReflectConstruct = function _isNativeReflectConstruct() { return !!t; })(); }
var RadioButton = _antd.Radio.Button;
var RadioGroup = _antd.Radio.Group;
var ValueSources = exports["default"] = /*#__PURE__*/function (_PureComponent) {
  function ValueSources() {
    var _this;
    (0, _classCallCheck2["default"])(this, ValueSources);
    for (var _len = arguments.length, args = new Array(_len), _key = 0; _key < _len; _key++) {
      args[_key] = arguments[_key];
    }
    _this = _callSuper(this, ValueSources, [].concat(args));
    _this.onChange = function (e) {
      var setValueSrc = _this.props.setValueSrc;
      setValueSrc(e.target.value);
    };
    return _this;
  }
  (0, _inherits2["default"])(ValueSources, _PureComponent);
  return (0, _createClass2["default"])(ValueSources, [{
    key: "render",
    value: function render() {
      var _this$props = this.props,
        config = _this$props.config,
        valueSources = _this$props.valueSources,
        valueSrc = _this$props.valueSrc,
        readonly = _this$props.readonly,
        title = _this$props.title;
      var content = /*#__PURE__*/_react["default"].createElement(RadioGroup, {
        value: valueSrc || "value",
        size: config.settings.renderSize,
        onChange: this.onChange,
        disabled: readonly
      }, valueSources.map(function (_ref) {
        var _ref2 = (0, _slicedToArray2["default"])(_ref, 2),
          srcKey = _ref2[0],
          info = _ref2[1];
        return /*#__PURE__*/_react["default"].createElement(RadioButton, {
          key: srcKey,
          value: srcKey
        }, info.label);
      }));
      return /*#__PURE__*/_react["default"].createElement("span", null, /*#__PURE__*/_react["default"].createElement(_antd.Popover, {
        content: content,
        title: title
      }, /*#__PURE__*/_react["default"].createElement(_icons.EllipsisOutlined, null)));
    }
  }]);
}(_react.PureComponent);